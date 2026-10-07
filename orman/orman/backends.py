from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveEmailBackend(ModelBackend):
    """Authenticate by email address, ignoring case.

    The stock ModelBackend looks users up with an exact match on
    USERNAME_FIELD, so "Mike@Example.com" wouldn't match a stored
    "mike@example.com" account. Since Person.USERNAME_FIELD is "email",
    people expect to log in regardless of how they capitalise it.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None
        try:
            user = UserModel._default_manager.get(
                **{f"{UserModel.USERNAME_FIELD}__iexact": username}
            )
        except UserModel.DoesNotExist:
            # Run the hasher anyway to avoid leaking, via timing, whether
            # the account exists (mirrors ModelBackend's own behaviour).
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
