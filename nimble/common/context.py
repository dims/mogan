# -*- encoding: utf-8 -*-
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from oslo_context import context


class RequestContext(context.RequestContext):
    """Extends security contexts from the oslo.context library."""

    def __init__(self, auth_token=None, domain_id=None, domain_name=None,
                 user_name=None, user_id=None, project_name=None,
                 project_id=None, is_admin=False, is_public_api=False,
                 read_only=False, show_deleted=False, request_id=None,
                 roles=None, show_password=True, overwrite=True, **kwargs):
        """Initialize the RequestContext

        :param auth_token: The authentication token of the current request.
        :param domain_id: The ID of the domain.
        :param domain_name: The name of the domain.
        :param user: The name of the user.
        :param tenant: The name of the tenant.
        :param is_admin: Indicates if the request context is an administrator
                         context.
        :param is_public_api: Specifies whether the request should be processed
                              without authentication.
        :param request_id: The UUID of the request.
        :param roles: List of user's roles if any.
        :param show_password: Specifies whether passwords should be masked
                              before sending back to API call.
        :param overwrite: Set to False to ensure that the greenthread local
                             copy of the index is not overwritten.
        """
        super(RequestContext, self).__init__(auth_token=auth_token,
                                             user=user_name,
                                             tenant=project_name,
                                             is_admin=is_admin,
                                             read_only=read_only,
                                             show_deleted=show_deleted,
                                             request_id=request_id,
                                             overwrite=overwrite)

        self.user_id = user_id
        self.user_name = user_name
        self.project_name = project_name
        self.project_id = project_id
        self.is_public_api = is_public_api
        self.domain_id = domain_id
        self.domain_name = domain_name
        self.show_password = show_password
        # NOTE(dims): roles was added in context.RequestContext recently.
        # we should pass roles in __init__ above instead of setting the
        # value here once the minimum version of oslo.context is updated.
        self.roles = roles or []

    def to_dict(self):
        value = super(RequestContext, self).to_dict()
        value.update({'auth_token': self.auth_token,
                      'user_name': self.user_name,
                      'user_id': self.user_id,
                      'project_name': self.project_name,
                      'project_id': self.project_id,
                      'is_admin': self.is_admin,
                      'read_only': self.read_only,
                      'show_deleted': self.show_deleted,
                      'request_id': self.request_id,
                      'domain_id': self.domain_id,
                      'roles': self.roles,
                      'domain_name': self.domain_name,
                      'show_password': self.show_password,
                      'is_public_api': self.is_public_api})
        return value

    @classmethod
    def from_dict(cls, values):
        return cls(**values)

    def ensure_thread_contain_context(self):
        """Ensure threading contains context

        For async/periodic tasks, the context of local thread is missing.
        Set it with request context and this is useful to log the request_id
        in log messages.

        """
        if context.get_current():
            return
        self.update_store()


def get_context(*args, **kwargs):
    return RequestContext(*args, **kwargs)


def get_admin_context():
    """Create an administrator context."""

    context = RequestContext(None,
                             project_id=None,
                             is_admin=True,
                             overwrite=False)
    return context
