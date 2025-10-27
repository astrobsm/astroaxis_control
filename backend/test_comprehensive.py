#!/usr/bin/env python3
"""
Comprehensive API Testing Script for ASTRO-ASIX ERP
Tests all endpoints, frontend serving, and application functionality
"""

import requests
import json
import sys
from typing import Dict, List
import time

class ERPTester:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url
        self.results = []
        
    def log_result(self, test_name: str, status: str, message: str, details: str = ""):
        """Log test results"""
        result = {
            "test": test_name,
            "status": status,
            "message": message,
            "details": details
        }
        self.results.append(result)
        
        # Print with color
        color = "\033[92m" if status == "PASS" else "\033[91m" if status == "FAIL" else "\033[93m"
        reset = "\033[0m"
        print(f"{color}[{status}]{reset} {test_name}: {message}")
        if details:
            print(f"      {details}")
    
    def test_health_endpoint(self):
        """Test health check endpoint"""
        try:
            response = requests.get(f"{self.base_url}/api/health", timeout=5)
            if response.status_code == 200:
                self.log_result("Health Check", "PASS", "Server is healthy", f"Response: {response.json()}")
            else:
                self.log_result("Health Check", "FAIL", f"Unexpected status code: {response.status_code}")
        except requests.exceptions.RequestException as e:
            self.log_result("Health Check", "FAIL", "Server not responding", str(e))
    
    def test_api_endpoint(self, endpoint: str, name: str):
        """Test a generic API endpoint"""
        try:
            response = requests.get(f"{self.base_url}{endpoint}", timeout=10)
            if response.status_code == 200:
                data = response.json()
                count = len(data.get('items', []) if isinstance(data, dict) else data)
                self.log_result(name, "PASS", f"Endpoint working", f"Returned {count} items")
            elif response.status_code == 404:
                self.log_result(name, "WARN", "Endpoint not found", f"Status: {response.status_code}")
            else:
                self.log_result(name, "FAIL", f"HTTP {response.status_code}", response.text[:100])
        except requests.exceptions.RequestException as e:
            self.log_result(name, "FAIL", "Connection error", str(e))
    
    def test_frontend_serving(self):
        """Test if frontend is being served properly"""
        try:
            # Test root route
            response = requests.get(f"{self.base_url}/", timeout=10)
            if response.status_code == 200:
                if "ASTRO-ASIX" in response.text:
                    self.log_result("Frontend Root", "PASS", "Frontend served successfully")
                else:
                    self.log_result("Frontend Root", "WARN", "Response received but content unclear")
            else:
                self.log_result("Frontend Root", "FAIL", f"HTTP {response.status_code}")
                
            # Test static assets
            static_files = ["/logo.png", "/favicon.ico", "/manifest.json"]
            for file_path in static_files:
                try:
                    resp = requests.get(f"{self.base_url}{file_path}", timeout=5)
                    if resp.status_code == 200:
                        self.log_result(f"Static File {file_path}", "PASS", "File served successfully")
                    else:
                        self.log_result(f"Static File {file_path}", "WARN", f"HTTP {resp.status_code}")
                except:
                    self.log_result(f"Static File {file_path}", "FAIL", "Failed to load")
                    
        except requests.exceptions.RequestException as e:
            self.log_result("Frontend Root", "FAIL", "Connection error", str(e))
    
    def test_api_documentation(self):
        """Test API documentation endpoints"""
        docs_endpoints = ["/docs", "/openapi.json"]
        for endpoint in docs_endpoints:
            try:
                response = requests.get(f"{self.base_url}{endpoint}", timeout=10)
                if response.status_code == 200:
                    self.log_result(f"Docs {endpoint}", "PASS", "Documentation accessible")
                else:
                    self.log_result(f"Docs {endpoint}", "FAIL", f"HTTP {response.status_code}")
            except requests.exceptions.RequestException as e:
                self.log_result(f"Docs {endpoint}", "FAIL", "Connection error", str(e))
    
    def run_comprehensive_tests(self):
        """Run all tests"""
        print("🚀 Starting Comprehensive ASTRO-ASIX ERP Tests")
        print("=" * 60)
        
        # 1. Health and basic connectivity
        self.test_health_endpoint()
        
        # 2. API Documentation
        self.test_api_documentation()
        
        # 3. Core API Endpoints
        api_tests = [
            ("/api/products/", "Products API"),
            ("/api/raw-materials/", "Raw Materials API"),
            ("/api/warehouses/", "Warehouses API"),
            ("/api/bom/", "BOM API"),
            ("/api/stock/movements/", "Stock Movements API"),
            ("/api/sales/customers/", "Customers API"),
            ("/api/sales/orders/", "Sales Orders API"),
            ("/api/production/orders/", "Production Orders API"),
            ("/api/staff/", "Staff API"),
            ("/api/debug/database-status/", "Database Status"),
        ]
        
        for endpoint, name in api_tests:
            self.test_api_endpoint(endpoint, name)
        
        # 4. Frontend serving tests
        self.test_frontend_serving()
        
        # 5. Summary
        self.print_summary()
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)
        
        total = len(self.results)
        passed = len([r for r in self.results if r["status"] == "PASS"])
        failed = len([r for r in self.results if r["status"] == "FAIL"])
        warnings = len([r for r in self.results if r["status"] == "WARN"])
        
        print(f"Total Tests: {total}")
        print(f"✅ Passed: {passed}")
        print(f"⚠️  Warnings: {warnings}")
        print(f"❌ Failed: {failed}")
        print(f"Success Rate: {(passed/total)*100:.1f}%")
        
        if failed > 0:
            print("\n❌ FAILED TESTS:")
            for result in self.results:
                if result["status"] == "FAIL":
                    print(f"  - {result['test']}: {result['message']}")
        
        if warnings > 0:
            print("\n⚠️  WARNINGS:")
            for result in self.results:
                if result["status"] == "WARN":
                    print(f"  - {result['test']}: {result['message']}")
        
        print("\n🎯 RECOMMENDATIONS:")
        if failed == 0:
            print("✅ All critical systems are operational!")
        else:
            print("🔧 Review failed tests and fix underlying issues")
            
        if passed >= total * 0.8:
            print("🚀 Application is ready for production!")
        else:
            print("⚠️  Application needs attention before production deployment")

if __name__ == "__main__":
    # Wait a moment for server to be ready
    print("⏳ Waiting for server to be ready...")
    time.sleep(2)
    
    tester = ERPTester()
    tester.run_comprehensive_tests()