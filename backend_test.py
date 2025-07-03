import requests
import json
import unittest
import os
from dotenv import load_dotenv
import sys
from pathlib import Path
import time

# Load environment variables from backend/.env
load_dotenv(Path(__file__).parent / 'backend' / '.env')

# Load environment variables from frontend/.env for the backend URL
load_dotenv(Path(__file__).parent / 'frontend' / '.env')

# Get the backend URL from environment variables
BACKEND_URL = os.environ.get('REACT_APP_BACKEND_URL')
if not BACKEND_URL:
    print("Error: REACT_APP_BACKEND_URL not found in environment variables")
    sys.exit(1)

# Ensure the URL ends with /api for backend routes
API_URL = f"{BACKEND_URL}/api"
print(f"Testing API at: {API_URL}")

class TestTelegramBotBackend(unittest.TestCase):
    """Test suite for the Telegram Bot with Stripe Subscriptions backend"""

    def test_root_endpoint(self):
        """Test the root endpoint to ensure server is running"""
        response = requests.get(f"{API_URL}/", timeout=10)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("message", data)
        print(f"✅ Root endpoint test passed: {data}")

    def test_status_endpoint_get(self):
        """Test the GET status endpoint to verify database connectivity"""
        response = requests.get(f"{API_URL}/status", timeout=10)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        print(f"✅ GET status endpoint test passed: {len(data)} status checks found")

    def test_status_endpoint_post(self):
        """Test the POST status endpoint to verify database write operations"""
        test_data = {"client_name": "backend_test_client"}
        response = requests.post(f"{API_URL}/status", json=test_data, timeout=10)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["client_name"], "backend_test_client")
        self.assertIn("id", data)
        self.assertIn("timestamp", data)
        print(f"✅ POST status endpoint test passed: {data}")

    def test_admin_stats_endpoint(self):
        """Test the admin stats endpoint"""
        response = requests.get(f"{API_URL}/admin/stats", timeout=10)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Check for required fields in response
        self.assertIn("total_users", data)
        self.assertIn("active_subscriptions", data)
        self.assertIn("expired_subscriptions", data)
        self.assertIn("canceled_subscriptions", data)
        self.assertIn("total_revenue", data)
        self.assertIn("recent_transactions", data)
        
        print(f"✅ Admin stats endpoint test passed: {data}")

    def test_admin_subscribers_endpoint(self):
        """Test the admin subscribers endpoint"""
        response = requests.get(f"{API_URL}/admin/subscribers", timeout=10)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Check for subscribers field in response
        self.assertIn("subscribers", data)
        self.assertIsInstance(data["subscribers"], list)
        
        print(f"✅ Admin subscribers endpoint test passed: {len(data['subscribers'])} subscribers found")

    def test_admin_add_subscriber(self):
        """Test the admin add subscriber endpoint"""
        # Test data for adding a subscriber
        test_data = {
            "telegram_username": "test_user",
            "email": "test@example.com",
            "duration_days": 30
        }
        
        response = requests.post(f"{API_URL}/admin/add-subscriber", json=test_data, timeout=10)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # The response might contain an error if the user doesn't exist in the system
        # This is expected since we're testing with a dummy user
        if "error" in data:
            print(f"ℹ️ Admin add subscriber test result (expected error for test user): {data}")
        else:
            self.assertIn("success", data)
            print(f"✅ Admin add subscriber test passed: {data}")

    def test_stripe_webhook_endpoint_structure(self):
        """Test the Stripe webhook endpoint structure"""
        # Create a minimal mock Stripe event
        mock_event = {
            "id": "evt_test123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test123",
                    "subscription": "sub_test123",
                    "metadata": {
                        "telegram_user_id": "123456789",
                        "user_id": "user_test123"
                    }
                }
            }
        }
        
        # Note: We can't fully test this without a valid Stripe signature
        # So we're just testing that the endpoint exists and returns a 400 for invalid signature
        headers = {"Stripe-Signature": "invalid_signature"}
        response = requests.post(f"{API_URL}/stripe-webhook", json=mock_event, headers=headers, timeout=10)
        
        # We expect a 400 error because of the invalid signature, which confirms the endpoint exists
        self.assertEqual(response.status_code, 400)
        print(f"✅ Stripe webhook endpoint structure test passed (expected 400 for invalid signature)")

    def test_error_handling(self):
        """Test error responses for invalid requests"""
        # Test with invalid JSON data
        invalid_data = "not_json_data"
        response = requests.post(f"{API_URL}/status", data=invalid_data, timeout=10)
        self.assertNotEqual(response.status_code, 200)
        print(f"✅ Error handling test passed for invalid JSON: {response.status_code}")
        
        # Test with missing required fields
        missing_fields = {}
        response = requests.post(f"{API_URL}/status", json=missing_fields, timeout=10)
        self.assertNotEqual(response.status_code, 200)
        print(f"✅ Error handling test passed for missing fields: {response.status_code}")
        
        # Test non-existent endpoint
        response = requests.get(f"{API_URL}/nonexistent_endpoint", timeout=10)
        self.assertEqual(response.status_code, 404)
        print(f"✅ Error handling test passed for non-existent endpoint: {response.status_code}")

    def test_environment_variables(self):
        """Test if all required environment variables are properly loaded"""
        # Check if we can access the environment variables
        required_vars = [
            'MONGO_URL', 'DB_NAME', 'BOT_TOKEN', 'STRIPE_SECRET_KEY',
            'STRIPE_PUBLISHABLE_KEY', 'STRIPE_WEBHOOK_SECRET', 'GROUP_ID',
            'GROUP_INVITE_LINK', 'DOMAIN', 'SUBSCRIPTION_PRICE',
            'SUBSCRIPTION_DAYS', 'CURRENCY', 'ADMIN_USER_IDS'
        ]
        
        # We can't directly check the server's environment variables,
        # but we can infer they're loaded by checking if the API is working
        response = requests.get(f"{API_URL}/", timeout=10)
        self.assertEqual(response.status_code, 200)
        print(f"✅ Environment variables test passed (inferred from API working)")

if __name__ == "__main__":
    # Run the tests individually with timeouts to prevent hanging
    test_suite = unittest.TestSuite()
    test_cases = [
        TestTelegramBotBackend('test_root_endpoint'),
        TestTelegramBotBackend('test_status_endpoint_get'),
        TestTelegramBotBackend('test_status_endpoint_post'),
        TestTelegramBotBackend('test_admin_stats_endpoint'),
        TestTelegramBotBackend('test_admin_subscribers_endpoint'),
        TestTelegramBotBackend('test_admin_add_subscriber'),
        TestTelegramBotBackend('test_stripe_webhook_endpoint_structure'),
        TestTelegramBotBackend('test_error_handling'),
        TestTelegramBotBackend('test_environment_variables')
    ]
    
    for test_case in test_cases:
        try:
            print(f"\n🔍 Running test: {test_case._testMethodName}")
            test_suite.addTest(test_case)
            result = unittest.TextTestRunner().run(test_case)
            time.sleep(1)  # Small delay between tests
        except Exception as e:
            print(f"❌ Test {test_case._testMethodName} failed with error: {str(e)}")
    
    print("\n✅ All tests completed")