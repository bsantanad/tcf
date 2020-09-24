#! /usr/bin/python
#
# Copyright (c) 2017-20 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
"""
File storage interface
----------------------

Export an interface on each target that allows the user to:

- upload files to the servr
- download files from the server
- remove said files form the server
- list them

each user has a separate storage area, flat in structure (no
subdirectories) which can be use to place blobs which might be removed
by the server after a certain time based on policy. Note these storage
areas are common to all the targets for each user.

Examples:

- upload files to the server than then other tools will
  use to (eg: burn into a  Flash ROM).

"""

import errno
import hashlib
import os
import re

import commonl
import ttbl

class interface(ttbl.tt_interface):

    def __init__(self):
        ttbl.tt_interface.__init__(self)

    def _target_setup(self, target, iface_name):
        pass

    def _release_hook(self, target, _force):
        pass

    _bad_path = re.compile(r"(^\.\.$|^\.\./|/\.\./|/\.\.$)")

    def _validate_file_path(self, file_path, user_path):
        matches = self._bad_path.findall(file_path)
        if matches:
            raise ValueError("%s: file path cannot contains components: "
                             "%s" % (file_path, " ".join(matches)))
        file_path_normalized = os.path.normpath(file_path)
        file_path_final = os.path.join(user_path, file_path_normalized)
        return file_path_final

    def get_list(self, _target, _who, args, _files, user_path):
        filenames = self.arg_get(args, 'filenames', list,
                                 allow_missing = True, default = [ ])
        file_data = {}
        def _list_filename(filename):
            file_path = os.path.join(user_path, filename)
            try:
                h = hashlib.sha256()
                commonl.hash_file(h, file_path)
                file_data[file_path[len(user_path) + 1:]] = h.hexdigest()
            except IOError as e:
                if e.errno != errno.ENOENT:
                    raise
                # the file does not exist, ignore it
        if filenames:
            for filename in filenames:
                if isinstance(filename, basestring):
                    _list_filename(filename)
        else:
            for _path, _dirnames, filenames in os.walk(user_path):
                for filename in filenames:
                    _list_filename(filename)
        file_data['result'] = dict(file_data)	# COMPAT
        return file_data

    def post_file(self, target, _who, args, files, user_path):
        file_path = self.arg_get(args, 'file_path', basestring)
        file_object = files['file']
        file_path_final = self._validate_file_path(file_path, user_path)
        commonl.makedirs_p(user_path)
        file_object.save(file_path_final)
        target.log.debug("%s: saved" % file_path_final)
        return dict()

    def get_file(self, _target, _who, args, _files, user_path):
        file_path = self.arg_get(args, 'file_path', basestring)
        file_path_final = self._validate_file_path(file_path, user_path)
        # interface core has file streaming support builtin
        # already, it will take care of streaming the file to the
        # client
        return dict(stream_file = file_path_final)

    def delete_file(self, _target, _who, args, _files, user_path):
        file_path = self.arg_get(args, 'file_path', basestring)
        file_path_final = self._validate_file_path(file_path, user_path)
        commonl.rm_f(file_path_final)
        return dict()
