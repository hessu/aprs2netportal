import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

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

        User = get_user_model()
        username = user_data['username']
        users = list(User.objects.filter(username__iexact=username)[:2])
        if len(users) > 1:
            logger.warning("Several users match callsign %s, not logging in", username)
            return None
        if users:
            user = users[0]
        elif settings.LOTW_AUTOCREATE_USERS:
            user = User(**user_data)
            user.set_unusable_password()
            user.save()
            logger.info("Created user %s from LoTW certificate", username)
        else:
            logger.info("No user for LoTW certificate callsign %s", username)
            return None

        if not self.user_can_authenticate(user):
            logger.warning("User %s is inactive", username)
            return None
        return user
