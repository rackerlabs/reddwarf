
from numbers import Number
import os
import re
import shutil
import time
import unittest

import pexpect

from nova import context
from nova import exception
from nova import volume

from proboscis import test
from proboscis.decorators import expect_exception
from proboscis.decorators import time_out
from tests import initialize
from tests.volumes import VOLUMES_DIRECT
from tests.util import test_config
from tests.util import poll_until
from nova.volume.san import ISCSILiteDriver


UUID_PATTERN = re.compile('^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                          '[0-9a-f]{4}-[0-9a-f]{12}$')

def is_uuid(text):
    return UUID_PATTERN.search(text) is not None


class StoryDetails(object):



    def __init__(self):
        self.api = volume.API()
        self.client = volume.Client()
        self.context = context.get_admin_context()
        self.device_path = None
        self.volume_desc = None
        self.volume_id = None
        self.volume_name = None
        self.volume = None
        self.host = "vagrant-host"
        self.original_uuid = None

    def get_volume(self):
        return self.api.get(self.context, self.volume_id)

    @property
    def mount_point(self):
        return "%s/%s" % (LOCAL_MOUNT_PATH, self.volume_id)

    @property
    def test_mount_file_path(self):
        return "%s/test.txt" % self.mount_point


story = None

LOCAL_MOUNT_PATH = "/testsmnt"


class VolumeTest(unittest.TestCase):
    """This test tells the story of a volume, from cradle to grave."""

    def __init__(self, *args, **kwargs):
        unittest.TestCase.__init__(self, *args, **kwargs)

    def setUp(self):
        global story
        self.story = story

    def assert_volume_as_expected(self, volume):
        self.assertTrue(isinstance(volume["id"], Number))
        self.assertEqual(self.story.volume_name, volume["display_name"])
        self.assertEqual(self.story.volume_desc, volume["display_description"])
        self.assertEqual(1, volume["size"])
        self.assertEqual(self.story.context.user_id, volume["user_id"])
        self.assertEqual(self.story.context.project_id, volume["project_id"])


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[initialize.Volume])
class SetUp(VolumeTest):

    def test_05_create_story(self):
        """Creating 'story' vars used by the rest of these tests."""
        global story
        story = StoryDetails()

    def test_10_wait_for_topics(self):
        """Wait until the volume topic is up before proceeding."""
        topics = ["volume"]
        from tests.util.topics import hosts_up
        while not all(hosts_up(topic) for topic in topics):
            pass

    def test_20_refresh_local_folders(self):
        """Delete the local folders used as mount locations if they exist."""
        if os.path.exists(LOCAL_MOUNT_PATH):
            #TODO(rnirmal): Also need to remove any existing mounts.
            shutil.rmtree(LOCAL_MOUNT_PATH)
        os.mkdir(LOCAL_MOUNT_PATH)
        # Give some time for the services to startup
        time.sleep(10)


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[SetUp])
class AddVolume(VolumeTest):

    def test_add(self):
        """Make call to prov. a volume and assert the return value is OK."""
        self.assertEqual(None, self.story.volume_id)
        name = "TestVolume"
        desc = "A volume that was created for testing."
        self.story.volume_name = name
        self.story.volume_desc = desc
        volume = self.story.api.create(self.story.context, size = 1,
                                       name=name, description=desc)
        self.assert_volume_as_expected(volume)
        self.assertTrue("creating", volume["status"])
        self.assertTrue("detached", volume["attach_status"])
        self.story.volume = volume
        self.story.volume_id = volume["id"]


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[AddVolume])
class AfterVolumeIsAdded(VolumeTest):
    """Check that the volume can be retrieved via the API, and setup.

    All we want to see returned is a list-like with an initial string.

    """

    @time_out(60)
    def test_api_get(self):
        """Wait until the volume is finished provisioning."""
        volume = poll_until(lambda : self.story.get_volume(),
                            lambda volume : volume["status"] != "creating")
        self.assertEqual(volume["status"], "available")
        self.assert_volume_as_expected(volume)
        self.assertTrue(volume["attach_status"], "detached")


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[AfterVolumeIsAdded])
class SetupVolume(VolumeTest):

    def test_assign_volume(self):
        """Tell the volume it belongs to this host node."""
        #TODO(tim.simpson) If this is important, could we add a test to
        #                  make sure some kind of exception is thrown if it
        #                  isn't added to certain drivers?
        self.assertNotEqual(None, self.story.volume_id)
        self.story.api.add_to_compute(self.story.context, self.story.volume_id,
                                      self.story.host)
        # TODO(rnirmal): remove once we fix discover retries
        time.sleep(5)

    def test_setup_volume(self):
        """Set up the volume on this host. AKA discovery."""
        self.assertNotEqual(None, self.story.volume_id)
        device = self.story.client.setup_volume(self.story.context,
                                                self.story.volume_id)
        if not isinstance(device, basestring):
            self.fail("Expected device to be a string, but instead it was " +
                      str(type(device)) + ".")
        self.story.device_path = device


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[SetupVolume])
class FormatVolume(VolumeTest):

    @expect_exception(IOError)
    def test_10_should_raise_IOError_if_format_fails(self):
        class BadFormatter(ISCSILiteDriver):

            def _format(self, device_path):
                pass

        bad_client = volume.Client(volume_driver=BadFormatter())
        bad_client._format(self.story.device_path)


    def test_20_format(self):
        self.assertNotEqual(None, self.story.device_path)
        self.story.client._format(self.story.device_path)


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[FormatVolume])
class MountVolume(VolumeTest):

    def test_mount(self):
        self.story.client._mount(self.story.device_path, self.story.mount_point)
        with open(self.story.test_mount_file_path, 'w') as file:
            file.write("Yep, it's mounted alright.")
        self.assertTrue(os.path.exists(self.story.test_mount_file_path))


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[MountVolume])
class UnmountVolume(VolumeTest):

    def test_unmount(self):
        self.story.client._unmount(self.story.mount_point)
        child = pexpect.spawn("sudo mount %s" % self.story.mount_point)
        child.expect("mount: can't find %s in" % self.story.mount_point)


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[UnmountVolume])
class GrabUuid(VolumeTest):

    def test_uuid_must_match_pattern(self):
        """UUID must be hex chars in the form 8-4-4-4-12."""
        client = self.story.client # volume.Client()
        device_path = self.story.device_path # '/dev/sda5'
        uuid = client.get_uuid(device_path)
        self.story.original_uuid = uuid
        self.assertTrue(is_uuid(uuid), "uuid must match regex")


    def test_get_invalid_uuid(self):
        """DevicePathInvalidForUuid is raised if device_path is wrong."""
        client = self.story.client
        device_path = "gdfjghsfjkhggrsyiyerreygghdsghsdfjhf"
        self.assertRaises(exception.DevicePathInvalidForUuid, client.get_uuid,
                          device_path)


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[GrabUuid])
class RemoveVolume(VolumeTest):

    def test_remove(self):
        self.story.client.remove_volume(self.story.context,
                                 self.story.volume_id)
        self.assertRaises(Exception,
                          self.story.client._format, self.story.device_path)

    def test_unassign_volume(self):
        self.assertNotEqual(None, self.story.volume_id)
        self.story.client.driver.unassign_volume(self.story.volume_id,
                                      self.story.host)


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[GrabUuid])
class Initialize(VolumeTest):

    @time_out(60)
    def test_10_initialize_will_format(self):
        """initialize will setup, format, and store the UUID of a volume"""
        self.assertTrue(self.story.get_volume()['uuid'] is None)
        self.story.client.initialize(self.story.context, self.story.volume_id)        
        volume = self.story.get_volume()
        self.assertTrue(is_uuid(volume['uuid']), "uuid must match regex")
        self.assertNotEqual(self.story.original_uuid, volume['uuid'],
                            "Validate our assumption that the volume UUID "
                            "will change when the volume is formatted.")

    @time_out(60)
    def test_20_initialize_the_second_time_will_not_format(self):
        """If initialize is called but a UUID exists, it should not format."""
        old_uuid = self.story.get_volume()['uuid']
        self.assertTrue(old_uuid is not None)

        self.story.client.remove_volume(self.story.context,
                                        self.story.volume_id)
        
        class VolumeClientNoFmt(volume.Client):

            def _format(self, device_path):
                raise RuntimeError("_format should not be called!")

        no_fmt_client = VolumeClientNoFmt()
        no_fmt_client.initialize(self.story.context, self.story.volume_id)
        self.assertEqual(old_uuid, self.story.get_volume()['uuid'],
                         "UUID should be the same as no formatting occurred.")


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[Initialize])
class DeleteVolume(VolumeTest):

    def test_delete(self):
        self.story.api.delete(self.story.context, self.story.volume_id)


@test(groups=[VOLUMES_DIRECT], depends_on_classes=[DeleteVolume])
class ConfirmMissing(VolumeTest):

    @expect_exception(Exception)
    def test_discover_should_fail(self):
        self.story.client.driver.discover_volume(self.story.context,
                                                self.story.volume)

    @time_out(60)
    def test_get_missing_volume(self):
        try:
            volume = poll_until(lambda : self.story.api.get(self.story.context,
                                                        self.story.volume_id),
                            lambda volume : volume["status"] != "deleted")
            self.assertEqual(volume["deleted"], False)
        except exception.VolumeNotFound:
            pass
