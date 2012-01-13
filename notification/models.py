from notification import backends
from django.db import models
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import get_language, activate, ugettext_lazy as _
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic
import settings


class NoticeType(models.Model):
    '''
    Stores a Notice class. Every notification sent out must belong to a
    specific class.
    '''
    label = models.CharField(_("label"), max_length=40)
    display = models.CharField(_("display"), max_length=50)
    description = models.CharField(_("description"), max_length=100)
    # by default only on for media with sensitivity less than or equal to this number
    default = models.IntegerField(_("default"))
    
    def __unicode__(self):
        return self.label
    
    class Meta:
        verbose_name = _("notice type")
        verbose_name_plural = _("notice types")


# XXX These lines must come AFTER NoticeType is defined
# key is a tuple (medium_id, backend_label)
NOTIFICATION_BACKENDS = backends.load_backends()
NOTICE_MEDIA = [key for key in NOTIFICATION_BACKENDS.keys()]
NOTICE_MEDIA_DEFAULTS = {key[0] : backend.spam_sensitivity for key,backend in \
                                                 NOTIFICATION_BACKENDS.items()}

def create_notice_type(label, display, description, default=2, verbosity=1):
    """
    Creates a new NoticeType.
    
    This is intended to be used by other apps as a post_syncdb manangement step.
    """
    try:
        notice_type = NoticeType.objects.get(label=label)
        updated = False
        if display != notice_type.display:
            notice_type.display = display
            updated = True
        if description != notice_type.description:
            notice_type.description = description
            updated = True
        if default != notice_type.default:
            notice_type.default = default
            updated = True
        if updated:
            notice_type.save()
            if verbosity > 0:
                print "Updated %s NoticeType" % label
    except NoticeType.DoesNotExist:
        NoticeType(label=label,
                   display=display,
                   description=description,
                   default=default).save()
        if verbosity > 0:
            print "Created %s NoticeType" % label

class NoticeSetting(models.Model):
    """
    Indicates, for a given user, whether to send notifications
    of a given NoticeType using a given medium.
    """
    
    user = models.ForeignKey(User, verbose_name=_("user"))
    notice_type = models.ForeignKey(NoticeType, verbose_name=_("notice type"))
    medium = models.CharField(_("medium"), max_length=1, choices=NOTICE_MEDIA)
    send = models.BooleanField(_("send"))
    
    class Meta:
        verbose_name = _("notice setting")
        verbose_name_plural = _("notice settings")
        unique_together = ("user", "notice_type", "medium")

def should_send(user, notice_type, medium):
    try:
        return NoticeSetting.objects.get(user = user,
                                         notice_type = notice_type,
                                         medium = medium).send
    except NoticeSetting.DoesNotExist:
        return NOTICE_MEDIA_DEFAULTS[medium] <= notice_type.default

class LanguageStoreNotAvailable(Exception):
    pass

def get_notification_language(user):
    """
    Returns site-specific notification language for this user. Raises
    LanguageStoreNotAvailable if this site does not use translated
    notifications.
    """
    if getattr(settings, "NOTIFICATION_LANGUAGE_MODULE", False):
        try:
            app_label, model_name = settings.NOTIFICATION_LANGUAGE_MODULE.split(".")
            model = models.get_model(app_label, model_name)
            language_model = model._default_manager.get(user__id__exact=user.id)
            if hasattr(language_model, "language"):
                return language_model.language
        except (ImportError, ImproperlyConfigured, model.DoesNotExist):
            raise LanguageStoreNotAvailable
    raise LanguageStoreNotAvailable

def send(users, label, extra_context={}, sender=None):
    """
    Creates a new notice.
    This is intended to be how other apps create new notices:
    notification.send(user, "friends_invite_sent", {"foo": "bar"})
    """
    notice_type = NoticeType.objects.get(label=label)
    current_language = get_language()

    for user in users:
        try:
            activate(get_notification_language(user))
        except LanguageStoreNotAvailable:
            pass
        
        for backend in NOTIFICATION_BACKENDS.values():
            if backend.can_send(user, notice_type):
                backend.deliver(user, sender, notice_type, extra_context)
    
    # reset environment to original language
    activate(current_language)

class ObservedItemManager(models.Manager):
    
    def all_for(self, observed, label):
        """
        Returns all ObservedItems for an observed object (everything obserting
        the object)
        """
        content_type = ContentType.objects.get_for_model(observed)
        observations = self.filter(content_type=content_type,
                                   object_id=observed.id,
                                   notice_type__label=label)
        return observations
    
    def get_for(self, observed, observer, label):
        """
        Returns an observation relationship between observer and observed,
        using the notification type of the given label
        """
        content_type = ContentType.objects.get_for_model(observed)
        observation = self.get(content_type=content_type,
                               object_id = observed.id,
                               user = observer,
                               label = label)
        return observation


class Observation(models.Model):
    """
    This works like a many to many table, defining observation relationships 
    between observers and observed objects.
    """
    user = models.ForeignKey(User, verbose_name=_("user"))
    notice_type = models.ForeignKey(NoticeType, verbose_name=_("notice type"))
    added = models.DateTimeField(_("added"), auto_now = True)
    objects = ObservedItemManager()
    # Polymorphic relation to allow any object to be observed
    content_type = models.ForeignKey(ContentType)                               
    object_id = models.PositiveIntegerField()                                   
    observed_object = generic.GenericForeignKey("content_type", "object_id") 

    def send_notice(self, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context.update({"observed": self.observed_object})
        send([self.user],
             self.notice_type.label,
             extra_context,
             sender = self.observed_object)

    class Meta:
        ordering = ["-added"]
        verbose_name = _("observed item")
        verbose_name_plural = _("observed items")
    
def observe(observed, observer, notice_type_label):
    """
    Create a new Observation
    To be used by applications to register a user as an observer for some object.
    """
    notice_type = NoticeType.objects.get(label=notice_type_label)
    observed_item = Observation(user=observer,
                                observed_object=observed,
                                notice_type=notice_type)
    observed_item.save()
    return observed_item

def stop_observing(observed, observer, label):
    """
    Remove an Observation
    """
    observed_item = ObservedItem.objects.get_for(observed, observer, label)
    observed_item.delete()

def send_observation_notices_for(observed, label, extra_context={}):
    """
    Send a Notice for each user observing this label at the observed object.
    """
    observations = Observation.objects.all_for(observed, label)
    for observation in observations:
        observation.send_notice(extra_context)

def is_observing(observed, observer, label):
    if observer.is_anonymous(): return False
    try:
        ObservedItem.objects.get_for(observed, observer, label)
        return True
    except ObservedItem.DoesNotExist:
        return False
    except ObservedItem.MultipleObjectsReturned:
        return True

