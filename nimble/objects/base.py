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

"""Nimble common internal object model"""

from oslo_utils import versionutils
from oslo_versionedobjects import base as object_base

from nimble import objects
from nimble.objects import fields as object_fields


class NimbleObjectRegistry(object_base.VersionedObjectRegistry):
    def registration_hook(self, cls, index):
        # NOTE(jroll): blatantly stolen from nova
        # NOTE(danms): This is called when an object is registered,
        # and is responsible for maintaining nimble.objects.$OBJECT
        # as the highest-versioned implementation of a given object.
        version = versionutils.convert_version_to_tuple(cls.VERSION)
        if not hasattr(objects, cls.obj_name()):
            setattr(objects, cls.obj_name(), cls)
        else:
            cur_version = versionutils.convert_version_to_tuple(
                getattr(objects, cls.obj_name()).VERSION)
            if version >= cur_version:
                setattr(objects, cls.obj_name(), cls)


class NimbleObject(object_base.VersionedObject):
    """Base class and object factory.

    This forms the base of all objects that can be remoted or instantiated
    via RPC. Simply defining a class that inherits from this base class
    will make it remotely instantiatable. Objects should implement the
    necessary "get" classmethod routines as well as "save" object methods
    as appropriate.
    """

    OBJ_SERIAL_NAMESPACE = 'nimble_object'
    OBJ_PROJECT_NAMESPACE = 'nimble'

    # TODO(lintan) Refactor these fields and create PersistentObject and
    # TimeStampObject like Nova when it is necessary.
    fields = {
        'created_at': object_fields.DateTimeField(nullable=True),
        'updated_at': object_fields.DateTimeField(nullable=True),
    }

    def as_dict(self):
        return dict((k, getattr(self, k))
                    for k in self.fields
                    if hasattr(self, k))

    def obj_refresh(self, loaded_object):
        """Applies updates for objects that inherit from base.NimbleObject.

        Checks for updated attributes in an object. Updates are applied from
        the loaded object column by column in comparison with the current
        object.
        """
        for field in self.fields:
            if (self.obj_attr_is_set(field) and
                    self[field] != loaded_object[field]):
                self[field] = loaded_object[field]

    @staticmethod
    def _from_db_object(obj, db_object):
        """Converts a database entity to a formal object.

        :param obj: An object of the class.
        :param db_object: A DB model of the object
        :return: The object of the class with the database entity added
        """

        for field in obj.fields:
            obj[field] = db_object[field]

        obj.obj_reset_changes()
        return obj


class NimbleObjectSerializer(object_base.VersionedObjectSerializer):
    # Base class to use for object hydration
    OBJ_BASE_CLASS = NimbleObject