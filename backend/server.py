from fastapi import FastAPI, APIRouter, HTTPException, Request, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import uuid
from datetime import datetime, timedelta
import asyncio
import threading
import stripe
from telegram import Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import json

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Configure Stripe
stripe.api_key = os.environ['STRIPE_SECRET_KEY']

# Bot configuration
BOT_TOKEN = os.environ['BOT_TOKEN']
GROUP_ID = int(os.environ['GROUP_ID'])
GROUP_INVITE_LINK = os.environ['GROUP_INVITE_LINK']
SUBSCRIPTION_PRICE = float(os.environ['SUBSCRIPTION_PRICE'])
SUBSCRIPTION_DAYS = int(os.environ['SUBSCRIPTION_DAYS'])
CURRENCY = os.environ['CURRENCY']
ADMIN_USER_IDS = [int(user_id) for user_id in os.environ['ADMIN_USER_IDS'].split(',')]
DOMAIN = os.environ['DOMAIN']

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Bot instance
bot = Bot(token=BOT_TOKEN)

# Scheduler for subscription checks
scheduler = AsyncIOScheduler()

# Define Models
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    telegram_user_id: int
    telegram_username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_admin: bool = False

class Subscription(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    telegram_user_id: int
    stripe_subscription_id: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    stripe_product_id: Optional[str] = None
    stripe_price_id: Optional[str] = None
    status: str = "pending"  # pending, active, canceled, expired
    amount: float
    currency: str
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class PaymentTransaction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    telegram_user_id: int
    stripe_session_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    amount: float
    currency: str
    status: str = "initiated"  # initiated, completed, failed, canceled
    metadata: Optional[Dict] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ManualSubscriptionAdd(BaseModel):
    telegram_username: str
    email: str
    duration_days: int = 30

# Telegram Bot Handlers
async def start_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    telegram_user_id = user.id
    
    # Check if user already exists
    existing_user = await db.users.find_one({"telegram_user_id": telegram_user_id})
    if not existing_user:
        # Create new user
        new_user = User(
            telegram_user_id=telegram_user_id,
            telegram_username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            is_admin=telegram_user_id in ADMIN_USER_IDS
        )
        await db.users.insert_one(new_user.dict())
    
    # Check subscription status
    subscription = await db.subscriptions.find_one(
        {"telegram_user_id": telegram_user_id, "status": "active"}
    )
    
    if subscription:
        await update.message.reply_text(
            f"✅ Привіт! У вас є активна підписка до {subscription['current_period_end'].strftime('%d.%m.%Y')}\n\n"
            f"Ви можете приєднатися до групи: {GROUP_INVITE_LINK}"
        )
    else:
        await update.message.reply_text(
            f"👋 Привіт! Вас вітає бот підписки.\n\n"
            f"💰 Вартість місячної підписки: {SUBSCRIPTION_PRICE} {CURRENCY}\n"
            f"📅 Тривалість: {SUBSCRIPTION_DAYS} днів\n\n"
            f"Для оформлення підписки натисніть кнопку нижче 👇",
            reply_markup=get_subscription_keyboard()
        )

def get_subscription_keyboard():
    """Get subscription keyboard"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = [
        [InlineKeyboardButton("💳 Оформити підписку", callback_data="subscribe")],
        [InlineKeyboardButton("ℹ️ Статус підписки", callback_data="status")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def button_callback(update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    telegram_user_id = user.id
    
    if query.data == "subscribe":
        # Create checkout session
        try:
            checkout_url = await create_stripe_checkout_session(telegram_user_id)
            await query.edit_message_text(
                f"💳 Для оплати підписки перейдіть за посиланням:\n\n{checkout_url}\n\n"
                f"Після оплати ви автоматично отримаєте доступ до групи."
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ Помилка при створенні платежу: {str(e)}\n\n"
                f"Спробуйте ще раз пізніше."
            )
    
    elif query.data == "status":
        subscription = await db.subscriptions.find_one(
            {"telegram_user_id": telegram_user_id, "status": "active"}
        )
        
        if subscription:
            await query.edit_message_text(
                f"✅ Ваша підписка активна\n"
                f"📅 Діє до: {subscription['current_period_end'].strftime('%d.%m.%Y %H:%M')}\n\n"
                f"Посилання на групу: {GROUP_INVITE_LINK}"
            )
        else:
            await query.edit_message_text(
                f"❌ У вас немає активної підписки\n\n"
                f"Для оформлення підписки натисніть кнопку нижче 👇",
                reply_markup=get_subscription_keyboard()
            )

async def admin_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin commands"""
    user = update.effective_user
    
    if user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ У вас немає доступу до адміністративних команд.")
        return
    
    # Get subscription statistics
    total_active = await db.subscriptions.count_documents({"status": "active"})
    total_expired = await db.subscriptions.count_documents({"status": "expired"})
    total_canceled = await db.subscriptions.count_documents({"status": "canceled"})
    
    # Get recent subscriptions
    recent_subs = await db.subscriptions.find(
        {"status": "active"},
        {"telegram_user_id": 1, "current_period_end": 1, "created_at": 1}
    ).sort("created_at", -1).limit(10).to_list(length=10)
    
    message = f"📊 Статистика підписок:\n\n"
    message += f"✅ Активних: {total_active}\n"
    message += f"❌ Скасованих: {total_canceled}\n"
    message += f"⏰ Закінчених: {total_expired}\n\n"
    
    if recent_subs:
        message += "📝 Останні активні підписки:\n"
        for sub in recent_subs:
            try:
                user_info = await bot.get_chat(sub['telegram_user_id'])
                username = user_info.username or f"ID{sub['telegram_user_id']}"
                message += f"@{username} - до {sub['current_period_end'].strftime('%d.%m.%Y')}\n"
            except:
                message += f"ID{sub['telegram_user_id']} - до {sub['current_period_end'].strftime('%d.%m.%Y')}\n"
    
    await update.message.reply_text(message)

async def create_stripe_checkout_session(telegram_user_id: int) -> str:
    """Create Stripe checkout session for subscription"""
    try:
        # Get or create customer
        user = await db.users.find_one({"telegram_user_id": telegram_user_id})
        if not user:
            raise Exception("User not found")
        
        # Create customer in Stripe
        customer = stripe.Customer.create(
            metadata={
                "telegram_user_id": str(telegram_user_id),
                "telegram_username": user.get("telegram_username", ""),
            }
        )
        
        # Create or get product
        products = stripe.Product.list(limit=1)
        if products.data:
            product = products.data[0]
        else:
            product = stripe.Product.create(
                name="Monthly Subscription",
                description="Monthly subscription access"
            )
        
        # Create or get price
        prices = stripe.Price.list(product=product.id, limit=1)
        if prices.data:
            price = prices.data[0]
        else:
            price = stripe.Price.create(
                unit_amount=int(SUBSCRIPTION_PRICE * 100),  # Convert to cents
                currency=CURRENCY.lower(),
                recurring={"interval": "month"},
                product=product.id,
            )
        
        # Create checkout session
        session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=["card"],
            line_items=[{
                "price": price.id,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{DOMAIN}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN}/cancel",
            metadata={
                "telegram_user_id": str(telegram_user_id),
                "user_id": user["id"]
            }
        )
        
        # Save transaction
        transaction = PaymentTransaction(
            user_id=user["id"],
            telegram_user_id=telegram_user_id,
            stripe_session_id=session.id,
            amount=SUBSCRIPTION_PRICE,
            currency=CURRENCY,
            status="initiated",
            metadata={"checkout_session_id": session.id}
        )
        await db.payment_transactions.insert_one(transaction.dict())
        
        return session.url
        
    except Exception as e:
        logging.error(f"Error creating checkout session: {str(e)}")
        raise

async def check_expired_subscriptions():
    """Check for expired subscriptions and remove users from group"""
    try:
        # Find expired subscriptions
        expired_subs = await db.subscriptions.find({
            "status": "active",
            "current_period_end": {"$lt": datetime.utcnow()}
        }).to_list(length=None)
        
        for sub in expired_subs:
            try:
                # Remove user from group
                await bot.ban_chat_member(
                    chat_id=GROUP_ID,
                    user_id=sub["telegram_user_id"]
                )
                
                # Immediately unban to allow future joins
                await bot.unban_chat_member(
                    chat_id=GROUP_ID,
                    user_id=sub["telegram_user_id"]
                )
                
                # Update subscription status
                await db.subscriptions.update_one(
                    {"id": sub["id"]},
                    {"$set": {"status": "expired", "updated_at": datetime.utcnow()}}
                )
                
                # Send notification to user
                await bot.send_message(
                    chat_id=sub["telegram_user_id"],
                    text=f"⏰ Ваша підписка закінчилася.\n\n"
                         f"Для продовження доступу до групи оформіть нову підписку: /start"
                )
                
                logging.info(f"Removed expired user {sub['telegram_user_id']} from group")
                
            except Exception as e:
                logging.error(f"Error removing user {sub['telegram_user_id']}: {str(e)}")
                
    except Exception as e:
        logging.error(f"Error checking expired subscriptions: {str(e)}")

# API Routes
@api_router.get("/")
async def root():
    return {"message": "Telegram Bot with Stripe Subscriptions"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

@api_router.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """Handle Stripe webhooks"""
    payload = await request.body()
    
    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, os.environ['STRIPE_WEBHOOK_SECRET']
        )
    except ValueError as e:
        logging.error(f"Invalid payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logging.error(f"Invalid signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        await handle_checkout_session_completed(session)
    
    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        await handle_subscription_updated(subscription)
    
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        await handle_subscription_deleted(subscription)
    
    elif event['type'] == 'invoice.payment_succeeded':
        invoice = event['data']['object']
        await handle_invoice_payment_succeeded(invoice)
    
    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        await handle_invoice_payment_failed(invoice)
    
    return {"status": "success"}

async def handle_checkout_session_completed(session):
    """Handle successful checkout session"""
    try:
        telegram_user_id = int(session['metadata']['telegram_user_id'])
        user_id = session['metadata']['user_id']
        
        # Get subscription details
        subscription = stripe.Subscription.retrieve(session['subscription'])
        
        # Update payment transaction
        await db.payment_transactions.update_one(
            {"stripe_session_id": session['id']},
            {"$set": {
                "status": "completed",
                "stripe_subscription_id": subscription.id,
                "updated_at": datetime.utcnow()
            }}
        )
        
        # Create or update subscription record
        sub_data = Subscription(
            user_id=user_id,
            telegram_user_id=telegram_user_id,
            stripe_subscription_id=subscription.id,
            stripe_customer_id=subscription.customer,
            stripe_product_id=subscription['items']['data'][0]['price']['product'],
            stripe_price_id=subscription['items']['data'][0]['price']['id'],
            status="active",
            amount=SUBSCRIPTION_PRICE,
            currency=CURRENCY,
            current_period_start=datetime.fromtimestamp(subscription.current_period_start),
            current_period_end=datetime.fromtimestamp(subscription.current_period_end),
            updated_at=datetime.utcnow()
        )
        
        await db.subscriptions.insert_one(sub_data.dict())
        
        # Send invite link to user
        await bot.send_message(
            chat_id=telegram_user_id,
            text=f"✅ Платіж успішний! Ваша підписка активна до {sub_data.current_period_end.strftime('%d.%m.%Y')}\n\n"
                 f"Приєднуйтесь до групи: {GROUP_INVITE_LINK}"
        )
        
        logging.info(f"Subscription activated for user {telegram_user_id}")
        
    except Exception as e:
        logging.error(f"Error handling checkout session: {str(e)}")

async def handle_subscription_updated(subscription):
    """Handle subscription updates"""
    try:
        # Update subscription in database
        await db.subscriptions.update_one(
            {"stripe_subscription_id": subscription.id},
            {"$set": {
                "status": subscription.status,
                "current_period_start": datetime.fromtimestamp(subscription.current_period_start),
                "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
                "updated_at": datetime.utcnow()
            }}
        )
        
        logging.info(f"Subscription {subscription.id} updated")
        
    except Exception as e:
        logging.error(f"Error updating subscription: {str(e)}")

async def handle_subscription_deleted(subscription):
    """Handle subscription cancellation"""
    try:
        # Update subscription status
        sub_record = await db.subscriptions.find_one({"stripe_subscription_id": subscription.id})
        if sub_record:
            await db.subscriptions.update_one(
                {"stripe_subscription_id": subscription.id},
                {"$set": {"status": "canceled", "updated_at": datetime.utcnow()}}
            )
            
            # Remove user from group
            await bot.ban_chat_member(
                chat_id=GROUP_ID,
                user_id=sub_record["telegram_user_id"]
            )
            
            await bot.unban_chat_member(
                chat_id=GROUP_ID,
                user_id=sub_record["telegram_user_id"]
            )
            
            # Notify user
            await bot.send_message(
                chat_id=sub_record["telegram_user_id"],
                text="❌ Ваша підписка була скасована. Доступ до групи припинено."
            )
            
        logging.info(f"Subscription {subscription.id} deleted")
        
    except Exception as e:
        logging.error(f"Error deleting subscription: {str(e)}")

async def handle_invoice_payment_succeeded(invoice):
    """Handle successful invoice payment (renewals)"""
    try:
        subscription = stripe.Subscription.retrieve(invoice.subscription)
        
        # Update subscription in database
        await db.subscriptions.update_one(
            {"stripe_subscription_id": subscription.id},
            {"$set": {
                "status": "active",
                "current_period_start": datetime.fromtimestamp(subscription.current_period_start),
                "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
                "updated_at": datetime.utcnow()
            }}
        )
        
        # Get user and send notification
        sub_record = await db.subscriptions.find_one({"stripe_subscription_id": subscription.id})
        if sub_record:
            await bot.send_message(
                chat_id=sub_record["telegram_user_id"],
                text=f"✅ Підписка продовжена! Діє до {datetime.fromtimestamp(subscription.current_period_end).strftime('%d.%m.%Y')}"
            )
        
        logging.info(f"Invoice payment succeeded for subscription {subscription.id}")
        
    except Exception as e:
        logging.error(f"Error handling invoice payment: {str(e)}")

async def handle_invoice_payment_failed(invoice):
    """Handle failed invoice payment"""
    try:
        subscription = stripe.Subscription.retrieve(invoice.subscription)
        
        # Get user and send notification
        sub_record = await db.subscriptions.find_one({"stripe_subscription_id": subscription.id})
        if sub_record:
            await bot.send_message(
                chat_id=sub_record["telegram_user_id"],
                text="❌ Помилка оплати підписки. Будь ласка, оновіть ваш спосіб оплати."
            )
        
        logging.info(f"Invoice payment failed for subscription {subscription.id}")
        
    except Exception as e:
        logging.error(f"Error handling invoice payment failure: {str(e)}")

# Admin API Routes
@api_router.get("/admin/subscribers")
async def get_subscribers():
    """Get all active subscribers"""
    try:
        subscribers = await db.subscriptions.find({"status": "active"}).to_list(length=None)
        
        # Enhance with user details
        enhanced_subscribers = []
        for sub in subscribers:
            user = await db.users.find_one({"id": sub["user_id"]})
            subscriber_info = {
                "id": sub["id"],
                "telegram_user_id": sub["telegram_user_id"],
                "telegram_username": user.get("telegram_username") if user else None,
                "email": user.get("email") if user else None,
                "current_period_end": sub["current_period_end"],
                "created_at": sub["created_at"],
                "amount": sub["amount"],
                "currency": sub["currency"]
            }
            enhanced_subscribers.append(subscriber_info)
        
        return {"subscribers": enhanced_subscribers}
        
    except Exception as e:
        logging.error(f"Error getting subscribers: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/admin/add-subscriber")
async def add_subscriber_manually(data: ManualSubscriptionAdd):
    """Manually add a subscriber"""
    try:
        # Try to find user by telegram username
        user = await db.users.find_one({"telegram_username": data.telegram_username})
        
        if not user:
            return {"error": "User not found. User must start the bot first."}
        
        # Check if user already has active subscription
        existing_sub = await db.subscriptions.find_one({
            "telegram_user_id": user["telegram_user_id"],
            "status": "active"
        })
        
        if existing_sub:
            return {"error": "User already has an active subscription."}
        
        # Create subscription
        end_date = datetime.utcnow() + timedelta(days=data.duration_days)
        
        subscription = Subscription(
            user_id=user["id"],
            telegram_user_id=user["telegram_user_id"],
            status="active",
            amount=SUBSCRIPTION_PRICE,
            currency=CURRENCY,
            current_period_start=datetime.utcnow(),
            current_period_end=end_date
        )
        
        await db.subscriptions.insert_one(subscription.dict())
        
        # Update user email if provided
        if data.email:
            await db.users.update_one(
                {"id": user["id"]},
                {"$set": {"email": data.email}}
            )
        
        # Send notification to user
        await bot.send_message(
            chat_id=user["telegram_user_id"],
            text=f"✅ Вам була надана підписка до {end_date.strftime('%d.%m.%Y')}\n\n"
                 f"Приєднуйтесь до групи: {GROUP_INVITE_LINK}"
        )
        
        return {"success": True, "message": "Subscriber added successfully"}
        
    except Exception as e:
        logging.error(f"Error adding subscriber: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/admin/stats")
async def get_admin_stats():
    """Get admin statistics"""
    try:
        total_users = await db.users.count_documents({})
        total_active_subs = await db.subscriptions.count_documents({"status": "active"})
        total_expired_subs = await db.subscriptions.count_documents({"status": "expired"})
        total_canceled_subs = await db.subscriptions.count_documents({"status": "canceled"})
        
        # Get recent transactions
        recent_transactions = await db.payment_transactions.find(
            {"status": "completed"}
        ).sort("created_at", -1).limit(10).to_list(length=10)
        
        # Calculate revenue
        total_revenue = await db.payment_transactions.aggregate([
            {"$match": {"status": "completed"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]).to_list(length=1)
        
        revenue = total_revenue[0]["total"] if total_revenue else 0
        
        return {
            "total_users": total_users,
            "active_subscriptions": total_active_subs,
            "expired_subscriptions": total_expired_subs,
            "canceled_subscriptions": total_canceled_subs,
            "total_revenue": revenue,
            "recent_transactions": recent_transactions
        }
        
    except Exception as e:
        logging.error(f"Error getting admin stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Initialize bot
async def init_bot():
    """Initialize the Telegram bot"""
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("admin", admin_command))
        
        # Add callback handler for buttons
        from telegram.ext import CallbackQueryHandler
        application.add_handler(CallbackQueryHandler(button_callback))
        
        # Initialize bot
        await application.initialize()
        await application.start()
        
        # Start polling in background
        await application.updater.start_polling()
        
        logging.info("Telegram bot initialized successfully")
        return application
        
    except Exception as e:
        logging.error(f"Error initializing bot: {str(e)}")
        raise

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global variables for bot and scheduler
telegram_app = None

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    global telegram_app
    
    try:
        # Initialize bot
        telegram_app = await init_bot()
        
        # Start scheduler
        scheduler.add_job(
            check_expired_subscriptions,
            IntervalTrigger(minutes=5),  # Check every 5 minutes
            id='check_expired_subscriptions'
        )
        scheduler.start()
        
        logging.info("All services initialized successfully")
        
    except Exception as e:
        logging.error(f"Error during startup: {str(e)}")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global telegram_app
    
    try:
        if telegram_app:
            await telegram_app.stop()
            await telegram_app.shutdown()
        
        if scheduler.running:
            scheduler.shutdown()
        
        client.close()
        
        logging.info("All services shut down successfully")
        
    except Exception as e:
        logging.error(f"Error during shutdown: {str(e)}")