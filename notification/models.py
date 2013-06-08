# Django
from django.db import models
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import get_language, activate, ugettext_lazy as _
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic
from django.conf import settings
from django.core.urlresolvers import reverse
from django.core.signing import Signer
from django.core.urlresolvers import resolve
from django.dispatch import receiver
from django.db.models.signals import pre_delete

# Django Apps
from django.contrib.sites.models import Site

# This app
from notification import backends

current_site = Site.objects.get_current()
root_url = "http://%s" % unicode(current_site)

class NoticeType(models.Model):
    '''
    Stores a Notice class. Every notification sent out must belong to a
    specific class.
    '''
    label = models.CharField(_("label"), max_length=40)
    display = models.CharField(_("display"), max_length=50)
    description = models.CharField(_("description"), max_length=100)
    # The nitice of this type will only get sent using a medium with span
    # sensitivity less than or equal than this number.
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
NOTICE_MEDIA_DEFAULTS = {key[0]: backend.spam_sensitivity for key, backend in
                                                 NOTIFICATION_BACKENDS.items()}
for key in NOTIFICATION_BACKENDS.keys():
    if key[1] == 'website':
        website = NOTIFICATION_BACKENDS[key]
        from notification.backends.website import Notice

def create_notice_type(label, display, description, default=2, verbosity=1):
    '''
    Creates a new NoticeType.
    Intended to be used by other apps as a post_syncdb manangement step.
    '''
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
    '''
    Object that indicates, for a given user, whether to send notifications
    of a given NoticeType using a given medium.
    '''

    user = models.ForeignKey(User, verbose_name=_("user"))
    notice_type = models.ForeignKey(NoticeType, verbose_name=_("notice type"))
    medium = models.CharField(_("medium"), max_length=1, choices=NOTICE_MEDIA)
    send = models.BooleanField(_("send"))

    class Meta:
        verbose_name = _("notice setting")
        verbose_name_plural = _("notice settings")
        unique_together = ("user", "notice_type", "medium")


def get_notification_setting(user, notice_type, medium):
    try:
        return NoticeSetting.objects.get(user=user,
                                         notice_type=notice_type,
                                         medium=medium)
    except NoticeSetting.DoesNotExist:
        send = NOTICE_MEDIA_DEFAULTS[medium] <= notice_type.default

        return NoticeSetting.objects.create(user=user,
                                            notice_type=notice_type,
                                            medium=medium,
                                            send=send)


def should_send(user, notice_type, medium):
    return get_notification_setting(user, notice_type, medium).send


class LanguageStoreNotAvailable(Exception):
    pass


def get_notification_language(user):
    '''
    Returns site-specific notification language for this user. Raises
    LanguageStoreNotAvailable if this site does not use translated
    notifications.
    '''
    if getattr(settings, "NOTIFICATION_LANGUAGE_MODULE", False):
        try:
            app_lbl, model_nm = settings.NOTIFICATION_LANGUAGE_MODULE.split(".")
            model = models.get_model(app_lbl, model_nm)
            language_model = model._default_manager.get(user__id__exact=user.id)
            if hasattr(language_model, "language"):
                return language_model.language
        except (ImportError, ImproperlyConfigured, model.DoesNotExist):
            raise LanguageStoreNotAvailable
    raise LanguageStoreNotAvailable


def broadcast(label, extra_context=None, sender=None, exclude=None):
    '''Brodcasts a notification for all the users on the system.'''

    extra_context = extra_context or {}
    exclude = exclude or []
    send_to = set(User.objects.all()) - set(exclude)

    send(send_to, label, extra_context, sender)

def get_sender_path(extra_context, sender):
        '''
        sender_path: a path to the sender. If not specified in extra_context then a url 
        will be generated automatically (/content_type/sender.id/) if your url's are the 
        same as your model names this should work.  If the website backend is present then 
        the sender_url will pass through the view_sender view and mark the notice as seen.
        *If specified in extra_context, provide just the path and it will be converted to
        the proper url automatically.
        
        NOTIFICATION_SENDER_URLS:provide a dictionary of sender to url overides.
        IE: if your content sender content_type user is located at path '/profie/' 
        then specify {'user':'profile'} sender_url will generate a link to this path
        when the word "user" or the translation is found in your notification description.
        '''
        sender_path = extra_context.get('sender_path', False)
        if not sender_path:
            #generate a path if not supplied in extra_conext
            ctype_translations = getattr(settings, 'NOTIFICATION_CONTENT_TYPE_TRANSLATIONS', {})
            try:
                ctype = ContentType.objects.get_for_model(sender)
                if ctype in ctype_translations:
                    ctype = ctype_translations['ctype']
                sender_path = '/'+str(ctype)+'/'+str(sender.id)+'/'
                resolve(sender_path)
            except:
                sender_path = ""
        return sender_path  
        
def send(users, label, extra_context=None, sender=None):
    '''
    Creates a new notice.
        This is intended to be how other apps create new notices:
        notification.send(user, "friends_invite_sent", {"foo": "bar"})
    
    sender: should always be the object of interest to the users(recipiants)
        Example 1:  if a user is followed the sender should be the following user.
        Example 2:  if a blog entry is commented on the sender should be the blog entry.
    '''
    
    notice_type = NoticeType.objects.get(label=label)
    current_language = get_language()
    extra_context = extra_context or {}
    notices_url = root_url + reverse("notification_notices")
    sender_path = get_sender_path(extra_context, sender)
   
    for user in users:
        try:
            activate(get_notification_language(user))
        except LanguageStoreNotAvailable:
            pass

        # generate unsubscribe link    
        signer = Signer()
        args = ['email', signer.sign(user.pk)]
        unsub_url = root_url + reverse('notificaton_unsubscribe', args=args)

        # update context with user specific translations
        context = {
            "recipient": user,
            "sender": sender,
            "notice": notice_type,
            "notices_url": notices_url,
            "root_url": root_url,
            "current_site": current_site,
            "unsubscribe_link": unsub_url,
        }

        #if website backend is present add context and save sender_path if provided.
        if website and website.can_send(user, notice_type):
            if sender_path:
                #save sender_path to website db
                extra_context.update({"sender_path":sender_path})
            website.deliver(user, sender, notice_type, extra_context)
            #website specific context
            #make sender_url with view_sender (TODO: this may be unreliable, may need signal after saved)
            notice = Notice.objects.latest('added')
            extra_context.update({"sender_url":root_url+notice.get_sender_url()})
            extra_context.update(notice.get_context())

        #if website is not present provide sender_url without view_sender.
        else:
            extra_context.update({"notice_id": False, "sender_url": root_url+sender_path})
        
        #add context that we did not want to get saved in website db
        extra_context.update(context)
        
        for backend in NOTIFICATION_BACKENDS.values():
            if backend.can_send(user, notice_type) and backend != website:
                backend.deliver(user, sender, notice_type, extra_context)

    # reset environment to original language
    activate(current_language)


class ObservedItemManager(models.Manager):

    def observers(self, observed, label):
        '''
        Returns all ObservedItems for an observed object (everything obserting
        the object)
        '''
        content_type = ContentType.objects.get_for_model(observed)
        observations = self.filter(content_type=content_type,
                                   object_id=observed.id,
                                   notice_type__label=label)
        return observations

    def get_for(self, observed, observer, label):
        '''
        Returns an observation relationship between observer and observed,
        using the notification type of the given label
        '''
        content_type = ContentType.objects.get_for_model(observed)
        observation = self.get(content_type=content_type,
                               object_id=observed.id,
                               user=observer,
                               notice_type__label=label)
        return observation


class Observation(models.Model):
    '''
    This works like a many to many table, defining observation relationships
    between observers and observed objects.
    '''
    user = models.ForeignKey(User, verbose_name=_("user"))
    notice_type = models.ForeignKey(NoticeType, verbose_name=_("notice type"))
    added = models.DateTimeField(_("added"), auto_now=True)
    objects = ObservedItemManager()
    # Polymorphic relation to allow any object to be observed
    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    observed_object = generic.GenericForeignKey("content_type", "object_id")
    send = models.BooleanField(_("send"), default=True)

    class meta:
        unique_toguether = ('user', 'notice_type', 'content_type', 'object_id')

    def send_notice(self, extra_context=None, sender=None):
        if self.send:
            if extra_context is None:
                extra_context = {}
            if not sender:
                sender = self.observed_object
                extra_context.update({"alter_desc":True})
            extra_context.update({"observed": self.observed_object})
            send([self.user],
                 self.notice_type.label,
                 extra_context,
                 sender=sender)

    class Meta:
        ordering = ["-added"]
        verbose_name = _("observed item")
        verbose_name_plural = _("observed items")
        
def observe(observed, observer, labels):
    '''
    Create a new Observation
    To be used by applications to register a user as an observer for some
    object.
    '''
    if not isinstance(labels, list):
        labels = [labels]
    for label in labels:
        if not is_observing(observed, observer, label):
            notice_type = NoticeType.objects.get(label=label)
            observed_item = Observation(user=observer,
                                        observed_object=observed,
                                        notice_type=notice_type)
            observed_item.save()


def stop_observing(observed, observer, labels):
    '''
    Remove an Observation
    '''
    if not isinstance(labels, list):
        labels = [labels]
    for label in labels:
        try:
            Observation.objects.get_for(observed, observer, label).delete()
        except Observation.DoesNotExist:
            pass


def send_observation_notices_for(observed, label, xcontext=None, exclude=None, sender=None):
    '''
    Send a Notice for each user observing this label at the observed object.
    context options:
    - alter_desc: determines if convert_to_observed_description occurs in template.
    optional kwargs:
    - sender: use to change the sender from the default observed object.
    '''
    xcontext = xcontext or {}
    exclude = exclude or []

    observations = Observation.objects.observers(observed, label)
    for observation in observations:
        if observation.user not in exclude:
            observation.send_notice(xcontext, sender=sender)


def is_observing(observed, observer, labels):
    if observer.is_anonymous():
        return False
    if not isinstance(labels, list):
        labels = [labels]
    for label in labels:
        try:
            Observation.objects.get_for(observed, observer, label)
        except Observation.DoesNotExist:
            return False
        except Observation.MultipleObjectsReturned:
            pass
    return True


def get_observations(observer, observed_type, labels):
    if observer.is_anonymous():
        return []
    if not isinstance(labels, list):
        labels = [labels]
    elements = set()
    for label in labels:
        content_type = ContentType.objects.get_for_model(observed_type)
        for x in Observation.objects.filter(user=observer,
                                            notice_type__label__in=labels,
                                            content_type=content_type):
            elements.add(x.observed_object)
    return list(elements)

#Delete observation objects when observed_object is deleted
content_types = Observation.objects.values('content_type__id').distinct()
content_types = set([c['content_type__id'] for c in content_types])
@receiver(pre_delete)
def observed_object_delete_handler(sender, *args, **kwargs):
    content_type = ContentType.objects.get_for_model(sender)
    if content_type.id in content_types:
        target = kwargs.pop('instance', None)
        observations = Observation.objects.filter(content_type=content_type, object_id=target.id)
        for o in observations:
            o.delete()