#!/usr/bin/env python3
"""
Test script to simulate Stripe webhook for testing purposes
"""

import requests
import json
import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
load_dotenv(Path(__file__).parent / 'backend' / '.env')
load_dotenv(Path(__file__).parent / 'frontend' / '.env')

BACKEND_URL = os.environ.get('REACT_APP_BACKEND_URL')
API_URL = f"{BACKEND_URL}/api"

def test_webhook():
    """Test webhook endpoint with mock data"""
    
    # Mock Stripe checkout session completed event
    mock_event = {
        "id": "evt_test_webhook",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123456",
                "object": "checkout.session",
                "payment_status": "paid",
                "subscription": "sub_test_123456",
                "metadata": {
                    "telegram_user_id": "123456789",
                    "user_id": "test_user_123"
                }
            }
        }
    }
    
    # Note: This will fail due to signature verification
    # but we can see if the endpoint is reachable
    headers = {
        "Stripe-Signature": "test_signature",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            f"{API_URL}/stripe-webhook",
            json=mock_event,
            headers=headers,
            timeout=10
        )
        
        print(f"Webhook endpoint response: {response.status_code}")
        print(f"Response body: {response.text}")
        
        # Expected: 400 due to invalid signature
        if response.status_code == 400:
            print("✅ Webhook endpoint is working (expected 400 for invalid signature)")
        else:
            print(f"❌ Unexpected response: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Error testing webhook: {str(e)}")

def test_payment_check():
    """Test payment status check endpoint"""
    
    try:
        response = requests.get(
            f"{API_URL}/check-payment/cs_test_123456",
            timeout=10
        )
        
        print(f"Payment check response: {response.status_code}")
        print(f"Response body: {response.text}")
        
    except Exception as e:
        print(f"❌ Error testing payment check: {str(e)}")

if __name__ == "__main__":
    print("Testing Stripe webhook integration...")
    print(f"API URL: {API_URL}")
    print("-" * 50)
    
    test_webhook()
    print("-" * 50)
    test_payment_check()