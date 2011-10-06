# Copyright 2010 OpenStack LLC.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from webob import exc

from nova import compute
from nova import log as logging
from nova.api.openstack import wsgi
from nova.compute import power_state
from nova.guest import api as guest_api
from nova.guest.db import models
from reddwarf.api import common
from reddwarf.db import api as dbapi


LOG = logging.getLogger('reddwarf.api.root')
LOG.setLevel(logging.DEBUG)


class Controller(object):
    """ Enable/Disable the root user for the DB Instance """

    def __init__(self):
        self.guest_api = guest_api.API()
        self.compute_api = compute.API()
        super(Controller, self).__init__()

    def create(self, req, instance_id, body):
        """ Enable the root user for the db instance """
        LOG.info("Call to enable root user for instance %s", instance_id)
        LOG.debug("%s - %s", req.environ, body)
        ctxt = req.environ['nova.context']
        common.instance_exists(ctxt, instance_id, self.compute_api)
        running = power_state.RUNNING
        status = dbapi.guest_status_get(instance_id).state
        if status != running:
            LOG.debug("Instance %s is not running." % instance_id)
            return exc.HTTPError("Instance %s is not running." % instance_id)
        try:
            result = self.guest_api.enable_root(ctxt, instance_id)
            user = models.MySQLUser()
            user.deserialize(result)
            dbapi.record_root_enabled_history(ctxt, instance_id, ctxt.user_id)
            return {'user': {'name': user.name, 'password': user.password}}
        except Exception as err:
            LOG.error(err)
            return exc.HTTPError("Error enabling the root password")

    def is_root_enabled(self, req, instance_id):
        """ Returns True if root is enabled for the given instance;
            False otherwise. """
        LOG.info("Call to is_root_enabled for instance %s", instance_id)
        LOG.debug("%s - %s", req.environ, req.body)
        ctxt = req.environ['nova.context']
        common.instance_exists(ctxt, instance_id, self.compute_api)
        running = power_state.RUNNING
        status = dbapi.guest_status_get(instance_id).state
        if status != running:
            LOG.debug("Instance %s is not running." % instance_id)
            return exc.HTTPError("Instance %s is not running." % instance_id)
        try:
            result = self.guest_api.is_root_enabled(ctxt, instance_id)
            return {'rootEnabled': result}
        except Exception as err:
            LOG.error(err)
            return exc.HTTPError("Error determining root access")

def create_resource(version='1.0'):
    controller = {
        '1.0': Controller,
    }[version]()

    metadata = {
        "attributes": {
            'user': ['name', 'password']
        },
    }

    xmlns = {
        '1.0': common.XML_NS_V10,
    }[version]

    serializers = {
        'application/xml': wsgi.XMLDictSerializer(metadata=metadata,
                                                  xmlns=xmlns),
    }

    response_serializer = wsgi.ResponseSerializer(body_serializers=serializers)

    return wsgi.Resource(controller, serializer=response_serializer)