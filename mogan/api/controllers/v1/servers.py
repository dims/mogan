# Copyright 2016 Huawei Technologies Co.,LTD.
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

import datetime

from oslo_log import log
from oslo_utils import netutils
import pecan
from pecan import rest
import six
from six.moves import http_client
import wsme
from wsme import types as wtypes

from mogan.api.controllers import base
from mogan.api.controllers import link
from mogan.api.controllers.v1.schemas import floating_ips as fip_schemas
from mogan.api.controllers.v1.schemas import interfaces as interface_schemas
from mogan.api.controllers.v1.schemas import remote_consoles as console_schemas
from mogan.api.controllers.v1.schemas import servers as server_schemas
from mogan.api.controllers.v1 import types
from mogan.api.controllers.v1 import utils as api_utils
from mogan.api import expose
from mogan.api import validation
from mogan.common import exception
from mogan.common.i18n import _
from mogan.common import policy
from mogan.common import states
from mogan import network
from mogan import objects

import re

_DEFAULT_SERVER_RETURN_FIELDS = ('uuid', 'name', 'description',
                                 'status', 'power_state')

_ONLY_ADMIN_VISIBLE_SEVER_FIELDS = ('node', 'affinity_zone',)

LOG = log.getLogger(__name__)


class ServerControllerBase(rest.RestController):
    _resource = None

    # This _resource is used for authorization.
    def _get_resource(self, uuid, *args, **kwargs):
        self._resource = objects.Server.get(pecan.request.context, uuid)
        return self._resource


class ServerStatesController(ServerControllerBase):

    _custom_actions = {
        'power': ['PUT'],
        'lock': ['PUT'],
        'provision': ['PUT'],
    }

    @policy.authorize_wsgi("mogan:server", "set_power_state")
    @expose.expose(None, types.uuid, wtypes.text,
                   status_code=http_client.ACCEPTED)
    def power(self, server_uuid, target):
        """Set the power state of the server.

        :param server_uuid: the UUID of a server.
        :param target: the desired target to change power state,
                       on, off or reboot.
        :raises Conflict (HTTP 409): if a power operation is
                 already in progress.
        :raises BadRequest (HTTP 400): if the requested target
                 state is not valid or if the server is in CLEANING state.

        """
        if target not in ["on", "off", "reboot", "soft_off", "soft_reboot"]:
            # ironic will throw InvalidStateRequested
            raise exception.InvalidActionParameterValue(
                value=target, action="power",
                server=server_uuid)

        db_server = self._resource or self._get_resource(server_uuid)
        pecan.request.engine_api.power(
            pecan.request.context, db_server, target)
        # At present we do not catch the Exception from ironicclient.
        # Such as Conflict and BadRequest.
        # varify provision_state, if server is being cleaned,
        # don't change power state?

        # Set the HTTP Location Header, user can get the power_state
        # by locaton.
        url_args = '/'.join([server_uuid, 'states'])
        pecan.response.location = link.build_url('servers', url_args)

    @policy.authorize_wsgi("mogan:server", "set_lock_state")
    @expose.expose(None, types.uuid, types.boolean,
                   status_code=http_client.ACCEPTED)
    def lock(self, server_uuid, target):
        """Set the lock state of the server.

        :param server_uuid: the UUID of a server.
        :param target: the desired target to change lock state,
                       true or false
        """
        db_server = self._resource or self._get_resource(server_uuid)
        context = pecan.request.context

        # Target is True, means lock a server
        if target:
            pecan.request.engine_api.lock(context, db_server)

        # Else, unlock the server
        else:
            # Try to unlock a server with non-admin or non-owner
            if not pecan.request.engine_api.is_expected_locked_by(
                    context, db_server):
                raise exception.Forbidden()
            pecan.request.engine_api.unlock(context, db_server)

    @policy.authorize_wsgi("mogan:server", "set_provision_state")
    @expose.expose(None, types.uuid, wtypes.text, types.uuid,
                   types.boolean, status_code=http_client.ACCEPTED)
    def provision(self, server_uuid, target, image_uuid=None,
                  preserve_ephemeral=None):
        """Asynchronous trigger the provisioning of the server.

        This will set the target provision state of the server, and
        a background task will begin which actually applies the state
        change. This call will return a 202 (Accepted) indicating the
        request was accepted and is in progress; the client should
        continue to GET the status of this server to observe the
        status of the requested action.

        :param server_uuid: UUID of a server.
        :param target: The desired provision state of the server or verb.
        :param image_uuid: UUID of the image rebuilt with.
        :param preserve_ephemeral: whether preserve the ephemeral parition.
        """

        # Currently we only support rebuild target
        if target not in (states.REBUILD,):
            raise exception.InvalidActionParameterValue(
                value=target, action="provision",
                server=server_uuid)
        db_server = self._resource or self._get_resource(server_uuid)
        if target == states.REBUILD:
            pecan.request.engine_api.rebuild(pecan.request.context, db_server,
                                             image_uuid, preserve_ephemeral)

        # Set the HTTP Location Header
        url_args = '/'.join([server_uuid, 'states'])
        pecan.response.location = link.build_url('servers', url_args)


class FloatingIPController(ServerControllerBase):
    """REST controller for Server floatingips."""

    def __init__(self, *args, **kwargs):
        super(FloatingIPController, self).__init__(*args, **kwargs)
        self.network_api = network.API()

    @policy.authorize_wsgi("mogan:server", "associate_floatingip")
    @expose.expose(None, types.uuid, body=types.jsontype,
                   status_code=http_client.NO_CONTENT)
    def post(self, server_uuid, floatingip):
        """Add(Associate) Floating Ip.

        :param server_uuid: UUID of a server.
        :param floatingip: The floating IP within the request body.
        """
        validation.check_schema(floatingip, fip_schemas.add_floating_ip)

        server = self._resource or self._get_resource(server_uuid)
        address = floatingip['address']
        server_nics = server.nics

        if not server_nics:
            msg = _('No ports associated to server')
            raise wsme.exc.ClientSideError(
                msg, status_code=http_client.BAD_REQUEST)

        fixed_address = None
        nic_to_associate = None
        if 'fixed_address' in floatingip:
            fixed_address = floatingip['fixed_address']
            for nic in server_nics:
                for port_address in nic.fixed_ips:
                    if port_address['ip_address'] == fixed_address:
                        nic_to_associate = nic
                        break
                else:
                    continue
                break
            else:
                msg = _('Specified fixed address not assigned to server')
                raise wsme.exc.ClientSideError(
                    msg, status_code=http_client.BAD_REQUEST)
        if nic_to_associate and nic_to_associate.floating_ip:
            msg = _('The specified fixed ip has already been associated with '
                    'a floating ip.')
            raise wsme.exc.ClientSideError(
                msg, status_code=http_client.CONFLICT)
        if not fixed_address:
            for nic in server_nics:
                if nic.floating_ip:
                    continue
                for port_address in nic.fixed_ips:
                    if netutils.is_valid_ipv4(port_address['ip_address']):
                        fixed_address = port_address['ip_address']
                        nic_to_associate = nic
                        break
                else:
                    continue
                break
            else:
                msg = _('Unable to associate floating IP %(address)s '
                        'to any fixed IPs for server %(id)s. '
                        'Server has no fixed IPv4 addresses to '
                        'associate or all fixed ips have already been '
                        'associated with floating ips.') % (
                    {'address': address, 'id': server.uuid})
                raise wsme.exc.ClientSideError(
                    msg, status_code=http_client.BAD_REQUEST)
            if len(server_nics) > 1:
                LOG.warning('multiple ports exist, using the first '
                            'IPv4 fixed_ip: %s', fixed_address)
        try:
            self.network_api.associate_floating_ip(
                pecan.request.context, floating_address=address,
                port_id=nic.port_id, fixed_address=fixed_address)
        except (exception.FloatingIpNotFoundForAddress,
                exception.Forbidden) as e:
            six.reraise(type(e), e)
        except Exception as e:
            msg = _('Unable to associate floating IP %(address)s to '
                    'fixed IP %(fixed_address)s for server %(id)s. '
                    'Error: %(error)s') % ({'address': address,
                                            'fixed_address': fixed_address,
                                            'id': server.uuid, 'error': e})
            LOG.exception(msg)
            raise wsme.exc.ClientSideError(
                msg, status_code=http_client.BAD_REQUEST)
        nic_to_associate.floating_ip = address
        nic_to_associate.save(pecan.request.context)

    @policy.authorize_wsgi("mogan:server", "disassociate_floatingip")
    @expose.expose(None, types.uuid, wtypes.text,
                   status_code=http_client.NO_CONTENT)
    def delete(self, server_uuid, address):
        """Dissociate floating_ip from a server.

        :param server_uuid: UUID of a server.
        :param floatingip: The floating IP within the request body.
        """
        if not netutils.is_valid_ipv4(address):
            msg = "Invalid IP address %s" % address
            raise wsme.exc.ClientSideError(
                msg, status_code=http_client.BAD_REQUEST)
        # get the floating ip object
        floating_ip = self.network_api.get_floating_ip_by_address(
            pecan.request.context, address)

        # get the associated server object (if any)
        try:
            server_id =\
                self.network_api.get_server_id_by_floating_address(
                    pecan.request.context, address)
        except (exception.FloatingIpNotFoundForAddress,
                exception.FloatingIpMultipleFoundForAddress) as e:
            six.reraise(type(e), e)

        # disassociate if associated
        if (floating_ip.get('port_id') and server_id == server_uuid):
            self.network_api.disassociate_floating_ip(pecan.request.context,
                                                      address)
            server = self._resource or self._get_resource(server_uuid)
            for nic in server.nics:
                if nic.floating_ip == address:
                    nic.floating_ip = None
                    nic.save(pecan.request.context)
        else:
            msg = _("Floating IP %(address)s is not associated with server "
                    "%(id)s.") % {'address': address, 'id': server_uuid}
            raise wsme.exc.ClientSideError(
                msg, status_code=http_client.BAD_REQUEST)


class InterfaceController(ServerControllerBase):
    def __init__(self, *args, **kwargs):
        super(InterfaceController, self).__init__(*args, **kwargs)

    @policy.authorize_wsgi("mogan:server", "attach_interface")
    @expose.expose(None, types.uuid, body=types.jsontype,
                   status_code=http_client.NO_CONTENT)
    def post(self, server_uuid, interface):
        """Attach Interface.

        :param server_uuid: UUID of a server.
        :param interface: The Baremetal Network ID within the request body.
        """
        validation.check_schema(interface, interface_schemas.attach_interface)

        port_id = interface.get('port_id', None)
        net_id = interface.get('net_id', None)

        server = self._resource or self._get_resource(server_uuid)
        pecan.request.engine_api.attach_interface(pecan.request.context,
                                                  server, net_id, port_id)

    @policy.authorize_wsgi("mogan:server", "detach_interface")
    @expose.expose(None, types.uuid, types.uuid,
                   status_code=http_client.NO_CONTENT)
    def delete(self, server_uuid, port_id):
        """Detach Interface

        :param server_uuid: UUID of a server.
        :param port_id: The Port ID within the request body.
        """
        server = self._resource or self._get_resource(server_uuid)
        server_nics = server.nics
        if port_id not in [nic.port_id for nic in server_nics]:
            raise exception.InterfaceNotFoundForServer(server=server_uuid)

        pecan.request.engine_api.detach_interface(pecan.request.context,
                                                  server, port_id)


class ServerNetworks(base.APIBase):
    """API representation of the networks of a server."""

    nics = types.jsontype
    """The instance nics information of the server"""

    def __init__(self, **kwargs):
        self.fields = ['nics']
        ret_nics = api_utils.show_nics(kwargs.get('nics') or [])
        super(ServerNetworks, self).__init__(nics=ret_nics)


class ServerNetworksController(ServerControllerBase):
    """REST controller for Server networks."""

    floatingips = FloatingIPController()
    """Expose floatingip as a sub-element of networks"""
    interfaces = InterfaceController()
    """Expose interface as a sub-element of networks"""

    @policy.authorize_wsgi("mogan:server", "get_networks")
    @expose.expose(ServerNetworks, types.uuid)
    def get(self, server_uuid):
        """List the networks info of the server.

        :param server_uuid: the UUID of a server.
        """
        db_server = self._resource or self._get_resource(server_uuid)
        return ServerNetworks(nics=db_server.nics.as_list_of_dict())


class Server(base.APIBase):
    """API representation of a server.

    This class enforces type checking and value constraints, and converts
    between the internal object model and the API representation of
    a server.
    """
    uuid = types.uuid
    """The UUID of the server"""

    name = wsme.wsattr(wtypes.text, mandatory=True)
    """The name of the server"""

    description = wtypes.text
    """The description of the server"""

    project_id = types.uuid
    """The project UUID of the server"""

    user_id = types.uuid
    """The user UUID of the server"""

    status = wtypes.text
    """The status of the server"""

    power_state = wtypes.text
    """The power state of the server"""

    availability_zone = wtypes.text
    """The availability zone of the server"""

    flavor_uuid = types.uuid
    """The server type UUID of the server"""

    image_uuid = types.uuid
    """The image UUID of the server"""

    addresses = types.jsontype
    """The addresses of the server"""

    links = wsme.wsattr([link.Link], readonly=True)
    """A list containing a self link"""

    launched_at = datetime.datetime
    """The UTC date and time of the server launched"""

    metadata = {wtypes.text: types.jsontype}
    """The meta data of the server"""

    fault = {wtypes.text: types.jsontype}
    """The fault of the server"""

    node = wtypes.text
    """The backend node of the server"""

    affinity_zone = wtypes.text
    """The affinity zone of the server"""

    key_name = wtypes.text
    """The ssh key name of the server"""

    partitions = types.jsontype
    """The partitions of the server"""

    locked = types.boolean
    """Represent the current lock state of the server"""

    def __init__(self, **kwargs):
        super(Server, self).__init__(**kwargs)
        self.fields = []
        for field in objects.Server.fields:
            if field == 'nics':
                addresses = api_utils.show_addresses(kwargs.get('nics') or [])
                setattr(self, 'addresses', addresses)
                continue
            if field == 'fault':
                if kwargs.get('status') != 'error':
                    setattr(self, field, wtypes.Unset)
                    continue
            if field in _ONLY_ADMIN_VISIBLE_SEVER_FIELDS:
                if not pecan.request.context.is_admin:
                    setattr(self, field, wtypes.Unset)
                    continue
            # Skip fields we do not expose.
            if not hasattr(self, field):
                continue
            self.fields.append(field)
            setattr(self, field, kwargs.get(field, wtypes.Unset))

    @classmethod
    def convert_with_links(cls, server_data, fields=None):
        server = Server(**server_data)
        server_uuid = server.uuid
        if fields is not None:
            server.unset_fields_except(fields)
        url = pecan.request.public_url
        server.links = [link.Link.make_link('self',
                                            url,
                                            'servers', server_uuid),
                        link.Link.make_link('bookmark',
                                            url,
                                            'servers', server_uuid,
                                            bookmark=True)
                        ]
        return server


class ServerPatchType(types.JsonPatchType):

    _api_base = Server

    @staticmethod
    def internal_attrs():
        defaults = types.JsonPatchType.internal_attrs()
        return defaults + ['/project_id', '/user_id', '/status',
                           '/power_state', '/availability_zone',
                           '/flavor_uuid', '/image_uuid', '/addresses',
                           '/launched_at', '/affinity_zone', '/key_name',
                           '/partitions', '/fault', '/node', '/locked']


class ServerCollection(base.APIBase):
    """API representation of a collection of server."""

    servers = [Server]
    """A list containing server objects"""

    @staticmethod
    def convert_with_links(servers_data, fields=None):
        collection = ServerCollection()
        collection.servers = [Server.convert_with_links(server, fields)
                              for server in servers_data]
        return collection


class ServerConsole(base.APIBase):
    """API representation of the console of a server."""

    protocol = wtypes.text
    """The protocol of the console"""

    type = wtypes.text
    """The type of the console"""

    url = wtypes.text
    """The url of the console"""

    @classmethod
    def sample(cls):
        sample = cls(
            protocol='serial', type='shellinabox',
            url='http://example.com/?token='
                'b4f5cb4a-8b01-40ea-ae46-67f0db4969b3')
        return sample


class ServerRemoteConsoleController(ServerControllerBase):
    """REST controller for Server."""

    @policy.authorize_wsgi("mogan:server", "create_remote_console")
    @expose.expose(ServerConsole, types.uuid, body=types.jsontype,
                   status_code=http_client.CREATED)
    def post(self, server_uuid, remote_console):
        """Get the serial console info of the server.

        :param server_uuid: the UUID of a server.
        :param remote_console: request body includes console type and protocol.
        """
        validation.check_schema(
            remote_console, console_schemas.create_console)

        server_obj = self._resource or self._get_resource(server_uuid)
        protocol = remote_console['protocol']
        console_type = remote_console['type']
        # Only serial console is supported now
        if protocol == 'serial':
            console_url = pecan.request.engine_api.get_serial_console(
                pecan.request.context, server_obj, console_type)

        return ServerConsole(protocol=protocol, type=console_type,
                             url=console_url['url'])


class ServerController(ServerControllerBase):
    """REST controller for Server."""

    states = ServerStatesController()
    """Expose the state controller action as a sub-element of servers"""

    networks = ServerNetworksController()
    """Expose the network controller action as a sub-element of servers"""

    remote_consoles = ServerRemoteConsoleController()
    """Expose the console controller of servers"""

    _custom_actions = {
        'detail': ['GET']
    }

    def _get_server_collection(self, name=None, status=None,
                               flavor_uuid=None, flavor_name=None,
                               image_uuid=None, ip=None,
                               all_tenants=None, fields=None):
        context = pecan.request.context
        project_only = True
        if context.is_admin and all_tenants:
            project_only = False

        filters = {}
        if name:
            filters['name'] = name
        if status:
            filters['status'] = status
        if flavor_uuid:
            filters['flavor_uuid'] = flavor_uuid
        if flavor_name:
            filters['flavor_name'] = flavor_name
        if image_uuid:
            filters['image_uuid'] = image_uuid

        servers = objects.Server.list(pecan.request.context,
                                      project_only=project_only,
                                      filters=filters)
        if ip:
            servers = self._ip_filter(servers, ip)

        servers_data = [server.as_dict() for server in servers]

        return ServerCollection.convert_with_links(servers_data,
                                                   fields=fields)

    @staticmethod
    def _ip_filter(servers, ip):
        ip = ip.replace('.', '\.')
        ip_obj = re.compile(ip)

        def _match_server(server):
            nw_info = server.nics
            for vif in nw_info:
                for fixed_ip in vif.fixed_ips:
                    address = fixed_ip.get('ip_address')
                    if not address:
                        continue
                    if ip_obj.match(address):
                        return True
            return False

        return filter(_match_server, servers)

    @expose.expose(ServerCollection, wtypes.text, wtypes.text,
                   types.uuid, wtypes.text, types.uuid, wtypes.text,
                   types.listtype, types.boolean)
    def get_all(self, name=None, status=None,
                flavor_uuid=None, flavor_name=None, image_uuid=None, ip=None,
                fields=None, all_tenants=None):
        """Retrieve a list of server.

        :param fields: Optional, a list with a specified set of fields
                       of the resource to be returned.
        :param all_tenants: Optional, allows administrators to see the
                            servers owned by all tenants, otherwise only the
                            servers associated with the calling tenant are
                            included in the response.
        """
        if fields is None:
            fields = _DEFAULT_SERVER_RETURN_FIELDS
        return self._get_server_collection(name, status,
                                           flavor_uuid, flavor_name,
                                           image_uuid, ip,
                                           all_tenants=all_tenants,
                                           fields=fields)

    @policy.authorize_wsgi("mogan:server", "get")
    @expose.expose(Server, types.uuid, types.listtype)
    def get_one(self, server_uuid, fields=None):
        """Retrieve information about the given server.

        :param server_uuid: UUID of a server.
        :param fields: Optional, a list with a specified set of fields
                       of the resource to be returned.
        """
        db_server = self._resource or self._get_resource(server_uuid)
        server_data = db_server.as_dict()

        return Server.convert_with_links(server_data, fields=fields)

    @expose.expose(ServerCollection, wtypes.text, wtypes.text,
                   types.uuid, wtypes.text, types.uuid, wtypes.text,
                   types.boolean)
    def detail(self, name=None, status=None,
               flavor_uuid=None, flavor_name=None, image_uuid=None, ip=None,
               all_tenants=None):
        """Retrieve detail of a list of servers."""
        # /detail should only work against collections
        cdict = pecan.request.context.to_policy_values()
        policy.authorize('mogan:server:get', cdict, cdict)
        parent = pecan.request.path.split('/')[:-1][-1]
        if parent != "servers":
            raise exception.NotFound()
        return self._get_server_collection(name, status,
                                           flavor_uuid, flavor_name,
                                           image_uuid, ip,
                                           all_tenants=all_tenants)

    @policy.authorize_wsgi("mogan:server", "create", False)
    @expose.expose(Server, body=types.jsontype,
                   status_code=http_client.CREATED)
    def post(self, server):
        """Create a new server.

        :param server: a server within the request body.
        """
        validation.check_schema(server, server_schemas.create_server)
        scheduler_hints = server.get('scheduler_hints', {})
        server = server.get('server')

        min_count = server.get('min_count', 1)
        max_count = server.get('max_count', min_count)

        if min_count > max_count:
            msg = _('min_count must be <= max_count')
            raise wsme.exc.ClientSideError(
                msg, status_code=http_client.BAD_REQUEST)

        requested_networks = server.pop('networks', None)
        flavor_uuid = server.get('flavor_uuid')
        image_uuid = server.get('image_uuid')
        user_data = server.get('user_data')
        key_name = server.get('key_name')
        partitions = server.get('partitions')
        personality = server.pop('personality', None)

        injected_files = []
        if personality:
            for item in personality:
                injected_files.append((item['path'], item['contents']))

        flavor = objects.Flavor.get(pecan.request.context, flavor_uuid)
        if flavor.disabled:
            raise exception.FlavorDisabled(flavor_id=flavor.uuid)
        servers = pecan.request.engine_api.create(
            pecan.request.context,
            flavor,
            image_uuid=image_uuid,
            name=server.get('name'),
            description=server.get('description'),
            availability_zone=server.get('availability_zone'),
            metadata=server.get('metadata'),
            requested_networks=requested_networks,
            user_data=user_data,
            injected_files=injected_files,
            key_name=key_name,
            min_count=min_count,
            max_count=max_count,
            partitions=partitions,
            scheduler_hints=scheduler_hints)
        # Set the HTTP Location Header for the first server.
        pecan.response.location = link.build_url('server', servers[0].uuid)
        return Server.convert_with_links(servers[0])

    @policy.authorize_wsgi("mogan:server", "update")
    @wsme.validate(types.uuid, [ServerPatchType])
    @expose.expose(Server, types.uuid, body=[ServerPatchType])
    def patch(self, server_uuid, patch):
        """Update a server.

        :param server_uuid: UUID of a server.
        :param patch: a json PATCH document to apply to this server.
        """
        db_server = self._resource or self._get_resource(server_uuid)
        try:
            server = Server(
                **api_utils.apply_jsonpatch(db_server.as_dict(), patch))

        except api_utils.JSONPATCH_EXCEPTIONS as e:
            raise exception.PatchError(patch=patch, reason=e)

        # Update only the fields that have changed
        for field in objects.Server.fields:
            if field == 'nics':
                continue
            try:
                patch_val = getattr(server, field)
            except AttributeError:
                # Ignore fields that aren't exposed in the API
                continue
            if patch_val == wtypes.Unset:
                patch_val = None
            if db_server[field] != patch_val:
                db_server[field] = patch_val

        db_server.save()

        return Server.convert_with_links(db_server.as_dict())

    @policy.authorize_wsgi("mogan:server", "delete")
    @expose.expose(None, types.uuid, status_code=http_client.NO_CONTENT)
    def delete(self, server_uuid):
        """Delete a server.

        :param server_uuid: UUID of a server.
        """
        db_server = self._resource or self._get_resource(server_uuid)
        pecan.request.engine_api.delete(pecan.request.context, db_server)
