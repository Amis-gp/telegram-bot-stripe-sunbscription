# Інструкція з налаштування Stripe Webhook для Telegram Bot

## Що виправлено:

1. **Success URL** - тепер перенаправляє назад до бота замість на адмін панель
2. **Додана обробка повернення з платежу** - бот розуміє коли користувач повертається після оплати
3. **Покращена обробка webhook-ів** - додані логи для відстеження подій
4. **Додано API для перевірки статусу платежу**

## Необхідні налаштування в Stripe Dashboard:

### 1. Створення Webhook Endpoint:

1. Увійдіть в Stripe Dashboard: https://dashboard.stripe.com/
2. Перейдіть до **Developers → Webhooks**
3. Натисніть **Add endpoint**
4. Введіть URL вашого webhook:
   ```
   https://50c6cc61-43fe-482d-9c6e-3257b6ea9a3d.preview.emergentagent.com/api/stripe-webhook
   ```
5. Виберіть події для відстеження:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`

### 2. Отримання Webhook Secret:

1. Після створення webhook endpoint, скопіюйте **Signing secret**
2. Замініть значення `STRIPE_WEBHOOK_SECRET` в файлі `/app/backend/.env`

### 3. Тестування продукту в Stripe:

1. Перейдіть до **Products** в Stripe Dashboard
2. Створіть продукт з назвою "Monthly Subscription"
3. Встановіть ціну 30 UAH з recurring billing "Monthly"
4. Використовуйте тестові картки:
   - Успішна оплата: `4242 4242 4242 4242`
   - Відмінена оплата: `4000 0000 0000 0002`

## Як працює новий флов:

1. **Користувач натискає "Оформити підписку"**
2. **Створюється Stripe checkout session**
3. **Success URL веде назад до бота** з параметром session_id
4. **Бот обробляє повернення** і перевіряє статус оплати
5. **Webhook активує підписку** в базі даних
6. **Користувач отримує посилання на групу**

## Важливо:

- Webhook може приходити з затримкою (до 5 хвилин)
- Тестові платежі не завжди генерують усі події
- Перевіряйте логи сервера для діагностики

## Перевірка логів:

```bash
tail -f /var/log/supervisor/backend.out.log
```

## Тестування:

1. Відправте `/start` боту в Telegram
2. Натисніть "Оформити підписку"
3. Використайте тестову картку для оплати
4. Перевірте, чи повертає вас назад до бота
5. Перевірте логи сервера на наявність webhook подій

Після налаштування webhook-ів система буде працювати повністю автоматично!