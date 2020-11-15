#! /usr/bin/python2
#
# Copyright (c) 2017 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
#

import filecmp
import hashlib
import os

import commonl.testing
import tcfl.tc

srcdir = os.path.dirname(__file__)
ttbd = commonl.testing.test_ttbd(config_files = [
    # strip to remove the compiled/optimized version -> get source
    os.path.join(srcdir, "conf_%s" % os.path.basename(__file__.rstrip('cd')))
])

@tcfl.tc.target(ttbd.url_spec + ' and t0')
class _test(tcfl.tc.tc_c):

    paths = set([	# same as in the config file
        "/path1",
        "/path2",
        "/path3",
        "/path4",
    ])

    def eval_list_root(self, target):
        r = target.store.list(path = "/")
        if 'result' in r:	# COMPAT
            del r['result']
        if set(r.keys()) != self.paths:
            raise tcfl.tc.failed_e(
                "root subdirectories doesn't match expected",
                dict(found = r, expected = self.paths))
        if set(r.values()) != set([ 'directory' ]):
            raise tcfl.tc.failed_e(
                "subdir entries are not all 'directory'",
                dict(found = r, expected = self.paths))
        self.report_pass("root level list includes expected subdirectories"
                         " and no files")


    def eval_list_default(self, target):
        r = target.store.list()
        r_subdirectories = set(k for k, v in r.items() if v == 'directory')
        if r_subdirectories:
            raise tcfl.tc.failed_e(
                "default list (user's) shall have no subdirectories",
                dict(found = r))
        self.report_pass("default list returns no subdirectories")


    def eval_list_path1(self, target):

        r = target.store.list(path = "/path1")
        r_subdirectories = set(k for k, v in r.items() if v == 'directory')
        if r_subdirectories:
            raise tcfl.tc.failed_e(
                "/path1 list (user's) shall have no subdirectories",
                dict(found = r))
        self.report_pass("/path1 list returns no subdirectories")



    def eval_list_path2(self, target):

        for digest in [ None, "md5", "sha256", "sha512" ]:
            r = target.store.list(path = "/path2", digest = digest)
            r_subdirectories = set(k for k, v in r.items() if v == 'directory')
            if r_subdirectories:
                raise tcfl.tc.failed_e(
                    "/path2 list (user's) shall have no subdirectories",
                    dict(found = r))
            if 'result' in r:
                del r['result']	# COMPAT
            if len(r) != 1:
                raise tcfl.tc.failed_e(
                    "/path2 list does not have expected length (1)",
                    dict(r = r))
            self.report_pass("/path2 list has expected length")
            if r.keys()[0] != 'fileA':
                raise tcfl.tc.failed_e(
                    "/path2/fileA not found",
                    dict(r = r))
            self.report_pass("/path2 lists fileA")
            local_filename = os.path.join(self.tmpdir, 'fileA')
            commonl.rm_f(local_filename)
            target.store.dnload("/path2/fileA", local_filename)
            if digest == None:
                digest = "sha256"
            h = hashlib.new(digest)
            commonl.hash_file(h, local_filename)
            if r['fileA'] != h.hexdigest():
                raise tcfl.tc.failed_e(
                    "/path2/fileA downloaded file's %s digest mismatch" % digest,
                    dict(found = h.hexdigest(), expected = r['fileA']))
            self.report_pass("/path2/fileA matches %s digest" % digest)



    def eval_list_path4(self, target):
        r = target.store.list(path = "/path4")
        if 'result' in r:	# COMPAT
            del r['result']
        r_subdirectories = set(r.keys())
        subdirs = set(["subdir1", "subdir2", "subdir3" ])
        if r_subdirectories != subdirs:
            raise tcfl.tc.failed_e(
                "/path4 lists the wrong subdirectories",
                dict(r = r, subdirs = subdirs))
        self.report_pass("/path1 list returns expected subdirectories")


    def eval_dnload_path_invalid(self, target):
        try:
            target.store.dnload("/etc/passwd", "/dev/null")
        except tcfl.tc.error_e as e:
            if 'tries to read from a location that is not allowed' not in str(e):
                raise tcfl.tc.failed_e(
                    "expected error with 'tries to read from a location that is not allowed'",
                    dict(e = e))
        target.report_pass("downloading a file out of allowed zones denied",
                           dict(e = e))

    @staticmethod
    def eval_dnload_non_existent(target):
        try:
            target.store.dnload("/path1/non_existing_file", "/dev/null")
        except tcfl.tc.error_e as e:
            if "can't stream file" not in str(e):
                raise tcfl.tc.failed_e(
                    "expected error with 'can't stream file'",
                    dict(e = e))
        target.report_pass("downloading non existing file fails",
                           dict(e = e))

    @staticmethod
    def eval_list_path_non_existent(target):
        try:
            target.store.list(path = "/path_invalid")
        except tcfl.tc.error_e as e:
            if "path not allowed" not in str(e):
                raise tcfl.tc.failed_e(
                    "expected error with 'path not allowed'",
                    dict(e = e))
        target.report_pass("listing invalid path fails",
                           dict(e = e))

    @staticmethod
    def eval_delete_non_allowed(target):
        try:
            target.store.delete("/path1/non_existing_file")
        except tcfl.tc.error_e as e:
            if "area that is not" not in str(e):
                raise tcfl.tc.failed_e(
                    "expected error with 'area that is not'",
                    dict(e = e))
        target.report_pass("deleting from invalid path fails",
                           dict(e = e))


    def eval_upload_delete(self, target):
        target.store.upload("test_file", __file__)
        local_filename = os.path.join(self.tmpdir, 'test_file')
        commonl.rm_f(local_filename)
        target.store.dnload("test_file", local_filename)
        if not filecmp.cmp(local_filename, __file__):
            raise tcfl.tc.failed_e("uploaded and downloaded file differ")
        target.store.delete("test_file")
        target.report_pass("can upload, download and delete")

    def eval_healcheck(self, target):
        target.store._healthcheck()
