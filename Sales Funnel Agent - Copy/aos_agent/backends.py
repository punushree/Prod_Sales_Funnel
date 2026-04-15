from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

User = get_user_model()


class EmailBackend(ModelBackend):
    """Authenticate using email + password (supports username= or email= kwarg)."""

    def authenticate(self, request, username=None, email=None, password=None, **kwargs):
        login_email = (email or username or "").strip().lower()
        if not login_email or not password:
            return None
        try:
            user = User.objects.get(email__iexact=login_email)
        except User.DoesNotExist:
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
