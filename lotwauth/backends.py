import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db import IntegrityError, transaction

from .dn import InvalidDN, user_data_from_dn

logger = logging.getLogger(__name__)


class LoTWCertificateBackend(ModelBackend):
    """Authenticates the holder of a verified LoTW client certificate."""

    def authenticate(self, request, lotw_dn=None, **kwargs):
        if lotw_dn is None:
            return None

        try:
            user_data = user_data_from_dn(lotw_dn)
        except InvalidDN as e:
            logger.warning("Rejecting client certificate DN %r: %s", lotw_dn, e)
            return None

        username = user_data['username']
        try:
            user = self.find_user(username)
        except get_user_model().MultipleObjectsReturned:
            logger.warning("Several users match callsign %s, not logging in", username)
            return None

        if user is None:
            if not settings.LOTW_AUTOCREATE_USERS:
                logger.info("No user for LoTW certificate callsign %s", username)
                return None
            user = self.create_user(user_data)
            if user is None:
                return None

        if not self.user_can_authenticate(user):
            logger.warning("User %s is inactive", username)
            return None
        return user

    def find_user(self, username):
        """Return the user with this callsign, compared case-insensitively."""
        User = get_user_model()
        try:
            return User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            return None

    def create_user(self, user_data):
        User = get_user_model()
        username = user_data['username']
        user = User(**user_data)
        user.set_unusable_password()
        try:
            # The savepoint keeps a failed insert from breaking a
            # transaction this may be running in.
            with transaction.atomic():
                user.save()
        except IntegrityError:
            # A browser opens several connections at once, and each of them
            # presents the certificate: another request created the user
            # between our lookup and our insert.
            try:
                return self.find_user(username)
            except User.MultipleObjectsReturned:
                return None
        logger.info("Created user %s from LoTW certificate", username)
        return user
