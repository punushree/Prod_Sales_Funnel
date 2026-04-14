"""
tests.py — AOS Agent Platform
Run with:  python manage.py test aos_agent
"""

from django.test import TestCase, Client
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Organisation, User, Lead, Customer, CallLog, Ticket
import uuid
from django.utils import timezone


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def make_org(name="Test Org", industry="real_estate"):
    return Organisation.objects.create(
        name=name, industry=industry,
        call_quota=100, minutes_quota=300,
    )

def make_user(email, role, org=None, password="testpass123"):
    user = User.objects.create_user(
        email=email, password=password,
        name="Test User", role=role, org=org,
    )
    return user

def get_jwt(user):
    """Returns Authorization header value for a user."""
    refresh = RefreshToken.for_user(user)
    return f"Bearer {str(refresh.access_token)}"


# ─────────────────────────────────────────────
# 1. MODEL TESTS
# ─────────────────────────────────────────────

class OrganisationModelTest(TestCase):

    def test_create_org(self):
        org = make_org()
        self.assertEqual(str(org.name), "Test Org")
        self.assertEqual(org.industry, "real_estate")
        self.assertFalse(org.quota_exceeded())

    def test_quota_exceeded(self):
        org = make_org()
        org.calls_used = 100
        org.save()
        self.assertTrue(org.quota_exceeded())

    def test_org_str(self):
        org = make_org()
        self.assertIn("Test Org", str(org))


class UserModelTest(TestCase):

    def test_create_super_admin(self):
        user = make_user("admin@test.com", "super_admin")
        self.assertEqual(user.role, "super_admin")
        self.assertIsNone(user.org)

    def test_create_org_admin(self):
        org = make_org()
        user = make_user("orgadmin@test.com", "org_admin", org=org)
        self.assertEqual(user.org, org)

    def test_user_str(self):
        user = make_user("agent@test.com", "agent")
        self.assertIn("agent@test.com", str(user))


class LeadModelTest(TestCase):

    def setUp(self):
        self.org  = make_org()
        self.user = make_user("admin@test.com", "org_admin", org=self.org)

    def test_create_lead(self):
        lead = Lead.objects.create(
            org=self.org, created_by=self.user,
            name="Rama Reddy", phone="9876543210",
            extra_data={"budget": "50L", "location": "Pune"},
        )
        self.assertEqual(lead.status, "new")
        self.assertEqual(lead.extra_data["budget"], "50L")

    def test_lead_str(self):
        lead = Lead.objects.create(
            org=self.org, created_by=self.user,
            name="Test Lead", phone="9999999999",
        )
        self.assertIn("Test Lead", str(lead))


# ─────────────────────────────────────────────
# 2. PERMISSION TESTS  (API)
# ─────────────────────────────────────────────

class PermissionTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.org1 = make_org("Org 1")
        self.org2 = make_org("Org 2")

        self.super_admin = make_user("super@test.com", "super_admin")
        self.org_admin1  = make_user("admin1@test.com", "org_admin", org=self.org1)
        self.org_admin2  = make_user("admin2@test.com", "org_admin", org=self.org2)
        self.agent       = make_user("agent@test.com",  "agent",     org=self.org1)

    # ── Org endpoints ──────────────────────────

    def test_super_admin_can_list_orgs(self):
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.super_admin))
        res = self.client.get("/api/v1/orgs/")
        self.assertEqual(res.status_code, 200)

    def test_org_admin_cannot_list_orgs(self):
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.org_admin1))
        res = self.client.get("/api/v1/orgs/")
        self.assertEqual(res.status_code, 403)

    def test_agent_cannot_list_orgs(self):
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.agent))
        res = self.client.get("/api/v1/orgs/")
        self.assertEqual(res.status_code, 403)

    def test_unauthenticated_denied(self):
        res = self.client.get("/api/v1/orgs/")
        self.assertEqual(res.status_code, 401)

    # ── Lead endpoints ─────────────────────────

    def test_org_admin_can_create_lead(self):
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.org_admin1))
        res = self.client.post("/api/v1/leads/", {
            "name": "New Lead", "phone": "1234567890",
        }, format="json")
        self.assertIn(res.status_code, [200, 201])

    def test_agent_cannot_create_lead(self):
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.agent))
        res = self.client.post("/api/v1/leads/", {
            "name": "Lead", "phone": "111",
        }, format="json")
        self.assertEqual(res.status_code, 403)

    def test_org_admin_cannot_see_other_org_lead(self):
        # Lead in org1
        lead = Lead.objects.create(
            org=self.org1, created_by=self.org_admin1,
            name="Org1 Lead", phone="0000000000",
        )
        # org_admin2 tries to access it
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.org_admin2))
        res = self.client.get(f"/api/v1/leads/{lead.id}/")
        self.assertEqual(res.status_code, 403)

    def test_super_admin_can_see_any_lead(self):
        lead = Lead.objects.create(
            org=self.org2, created_by=self.org_admin2,
            name="Org2 Lead", phone="9999999999",
        )
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.super_admin))
        res = self.client.get(f"/api/v1/leads/{lead.id}/")
        self.assertEqual(res.status_code, 200)

    # ── Ticket endpoints ───────────────────────

    def test_agent_can_create_ticket(self):
        customer = Customer.objects.create(
            org=self.org1, name="Cust", phone="8888888888",
        )
        call = CallLog.objects.create(
            org=self.org1, customer=customer,
            call_type="service", direction="inbound",
            started_at=timezone.now(),
        )
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.agent))
        res = self.client.post("/api/v1/tickets/", {
            "org": str(self.org1.id),
            "customer": str(customer.id),
            "call": str(call.id),
            "issue_type": "plumbing",
            "description": "Water leaking in flat B-204",
        }, format="json")
        self.assertIn(res.status_code, [200, 201])

    def test_agent_cannot_delete_ticket(self):
        customer = Customer.objects.create(
            org=self.org1, name="Cust", phone="7777777777",
        )
        ticket = Ticket.objects.create(
            org=self.org1, customer=customer,
            issue_type="plumbing", description="Leak",
        )
        self.client.credentials(HTTP_AUTHORIZATION=get_jwt(self.agent))
        res = self.client.delete(f"/api/v1/tickets/{ticket.id}/")
        self.assertEqual(res.status_code, 403)


# ─────────────────────────────────────────────
# 3. AUTH TESTS (HTML views)
# ─────────────────────────────────────────────

class AuthViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.org  = make_org()
        self.user = make_user("login@test.com", "org_admin", org=self.org)

    def test_login_page_loads(self):
        res = self.client.get("/login/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Sign in")

    def test_login_with_valid_credentials(self):
        res = self.client.post("/login/", {
            "email": "login@test.com",
            "password": "testpass123",
        })
        self.assertRedirects(res, "/")

    def test_login_with_wrong_password(self):
        res = self.client.post("/login/", {
            "email": "login@test.com",
            "password": "wrongpassword",
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Invalid")

    def test_dashboard_requires_login(self):
        res = self.client.get("/")
        self.assertRedirects(res, "/login/?next=/")

    def test_logout_redirects_to_login(self):
        self.client.login(username="login@test.com", password="testpass123")
        res = self.client.get("/logout/")
        self.assertRedirects(res, "/login/")


# ─────────────────────────────────────────────
# 4. LEAD FORM TESTS (HTML views)
# ─────────────────────────────────────────────

class LeadViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.org  = make_org()
        self.user = make_user("admin@test.com", "org_admin", org=self.org)
        self.client.login(username="admin@test.com", password="testpass123")

    def test_lead_list_loads(self):
        res = self.client.get("/leads/")
        self.assertEqual(res.status_code, 200)

    def test_create_lead_via_form(self):
        res = self.client.post("/leads/create/", {
            "name":  "Rama Reddy",
            "phone": "9876543210",
            "email": "rama@test.com",
        })
        self.assertRedirects(res, "/leads/")
        self.assertTrue(Lead.objects.filter(name="Rama Reddy").exists())

    def test_lead_detail_loads(self):
        lead = Lead.objects.create(
            org=self.org, created_by=self.user,
            name="Detail Lead", phone="1111111111",
        )
        res = self.client.get(f"/leads/{lead.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Detail Lead")

    def test_update_lead_status(self):
        lead = Lead.objects.create(
            org=self.org, created_by=self.user,
            name="Status Lead", phone="2222222222",
        )
        res = self.client.post(f"/leads/{lead.id}/status/", {
            "status": "interested",
            "notes":  "Very interested in 2BHK",
        })
        self.assertRedirects(res, f"/leads/{lead.id}/")
        lead.refresh_from_db()
        self.assertEqual(lead.status, "interested")


# ─────────────────────────────────────────────
# 5. QUOTA TEST
# ─────────────────────────────────────────────

class QuotaTest(TestCase):

    def test_quota_not_exceeded_initially(self):
        org = make_org()
        self.assertFalse(org.quota_exceeded())

    def test_quota_exceeded_on_calls(self):
        org = make_org()
        org.calls_used = 100   # equal to call_quota
        org.save()
        self.assertTrue(org.quota_exceeded())

    def test_quota_exceeded_on_minutes(self):
        org = make_org()
        org.minutes_used = 301  # over minutes_quota
        org.save()
        self.assertTrue(org.quota_exceeded())