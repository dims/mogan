# Copyright 2016 Intel, Inc.
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

import jsonpatch
from oslo_config import cfg
import wsme

from mogan.common.i18n import _


CONF = cfg.CONF


JSONPATCH_EXCEPTIONS = (jsonpatch.JsonPatchException,
                        jsonpatch.JsonPointerException,
                        KeyError)


def validate_limit(limit):
    if limit is None:
        return CONF.api.max_limit

    if limit <= 0:
        raise wsme.exc.ClientSideError(_("Limit must be positive"))

    return min(CONF.api.max_limit, limit)


def validate_sort_dir(sort_dir):
    if sort_dir not in ['asc', 'desc']:
        raise wsme.exc.ClientSideError(_("Invalid sort direction: %s. "
                                         "Acceptable values are "
                                         "'asc' or 'desc'") % sort_dir)
    return sort_dir


def apply_jsonpatch(doc, patch):
    for p in patch:
        if p['op'] == 'add' and p['path'].count('/') == 1:
            if p['path'].lstrip('/') not in doc:
                msg = _('Adding a new attribute (%s) to the root of '
                        ' the resource is not allowed')
                raise wsme.exc.ClientSideError(msg % p['path'])
    return jsonpatch.apply_patch(doc, jsonpatch.JsonPatch(patch))


def show_addresses(nics):
    addresses = {}
    for nic in nics:
        address = []
        for fixed_ip in nic.get('fixed_ips', []):
            address.append({'addr': fixed_ip['ip_address'],
                            'type': 'fixed'})
        if nic.get('floating_ip'):
            address.append({'addr': nic['floating_ip'],
                            'type': 'floating'})
        key = nic.get('network_id')
        if addresses.get(key):
            addresses[key] = addresses[key] + address
        else:
            addresses[key] = address
    return addresses


def show_nics(nics):
    show_keys = ['port_id', 'network_id', 'mac_address',
                 'fixed_ips', 'floating_ip', 'extra']
    ret_nics = []
    for nic in nics:
        ret_nics.append({key: value for key, value in nic.items() if key in
                         show_keys})
    return ret_nics
