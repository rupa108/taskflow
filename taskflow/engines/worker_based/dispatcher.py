# -*- coding: utf-8 -*-

#    Copyright (C) 2014 Yahoo! Inc. All Rights Reserved.
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

import logging

from kombu import exceptions as kombu_exc
import six

LOG = logging.getLogger(__name__)


class TypeDispatcher(object):
    """Receives messages and dispatches to type specific handlers."""

    def __init__(self, type_handlers):
        self._handlers = dict(type_handlers)
        self._requeue_filters = []

    def add_requeue_filter(self, callback):
        """Add a callback that can *request* message requeuing.

        The callback will be activated before the message has been acked and
        it can be used to instruct the dispatcher to requeue the message
        instead of processing it.
        """
        assert six.callable(callback), "Callback must be callable"
        self._requeue_filters.append(callback)

    def _collect_requeue_votes(self, data, message):
        # Returns how many of the filters asked for the message to be requeued.
        requeue_votes = 0
        for f in self._requeue_filters:
            try:
                if f(data, message):
                    requeue_votes += 1
            except Exception:
                LOG.exception("Failed calling requeue filter to determine"
                              " if message %r should be requeued.",
                              message.delivery_tag)
        return requeue_votes

    def _requeue_log_error(self, message, errors):
        # TODO(harlowja): Remove when http://github.com/celery/kombu/pull/372
        # is merged and a version is released with this change...
        try:
            message.requeue()
        except errors as exc:
            # This was taken from how kombu is formatting its messages
            # when its reject_log_error or ack_log_error functions are
            # used so that we have a similar error format for requeuing.
            LOG.critical("Couldn't requeue %r, reason:%r",
                         message.delivery_tag, exc, exc_info=True)
        else:
            LOG.debug("AMQP message %r requeued.", message.delivery_tag)

    def on_message(self, data, message):
        """This method is called on incoming messages."""
        LOG.debug("Got message: %r", message.delivery_tag)
        if self._collect_requeue_votes(data, message):
            self._requeue_log_error(message,
                                    errors=(kombu_exc.MessageStateError,))
        else:
            try:
                msg_type = message.properties['type']
            except KeyError:
                message.reject_log_error(
                    logger=LOG, errors=(kombu_exc.MessageStateError,))
                LOG.warning("The 'type' message property is missing"
                            " in message %r", message.delivery_tag)
            else:
                handler = self._handlers.get(msg_type)
                if handler is None:
                    message.reject_log_error(
                        logger=LOG, errors=(kombu_exc.MessageStateError,))
                    LOG.warning("Unexpected message type: '%s' in message"
                                " %r", msg_type, message.delivery_tag)
                else:
                    message.ack_log_error(
                        logger=LOG, errors=(kombu_exc.MessageStateError,))
                    if message.acknowledged:
                        LOG.debug("AMQP message %r acknowledged.",
                                  message.delivery_tag)
                        handler(data, message)
