#! /usr/bin/python2
#
# Copyright (c) 2017 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
#
"""

"""
import bisect
import collections
import getpass
import json
import logging
import os
import pprint
import sys
import socket
import time

import requests
import requests.exceptions
import tabulate

import commonl
import tc
import ttb_client
from . import msgid_c

asciimatics_support = True
try:
    import asciimatics.widgets
    import asciimatics.event
    import asciimatics.scene
except ImportError as e:
    asciimatics_support = False
    

def _delete(rtb, allocid):
    try:
        rtb.send_request("DELETE", "allocation/%s" % allocid)
    except requests.ConnectionError as e:
        # this server is out
        logging.warning(e)
        return
    except requests.HTTPError as e:
        if 'invalid allocation' not in str(e):
            raise
        # FIXME: HACK: this means invalid allocation,
        # already wiped


# FIXME: what happens if the target is disabled / removed while we wait
# FIXME: what happens if the conn
def _alloc_targets(rtb, groups, obo = None, keepalive_period = 4,
                   queue_timeout = None, priority = 700, preempt = False,
                   queue = True, reason = None, wait_in_queue = True):
    assert isinstance(groups, dict)

    data = dict(
        priority = priority,
        preempt = preempt,
        queue = queue,
        groups = {},
        reason = reason,
    )
    if obo:
        data['obo_user'] = obo
    data['groups'] = groups
    r = rtb.send_request("PUT", "allocation", json = data)

    ts0 = time.time()
    state = r['state']
    if state not in ( 'queued', 'active'):
        raise RuntimeError(
            "allocation failed: %s: %s"
            % (state, r.get('_message', 'message n/a')))
    allocid = r['allocid']
    data = { allocid: state }
    if state == 'active':			# got it
        return allocid, state, r['group_allocated'].split(',')
    if queue_timeout == 0:
        return allocid, state, {}
    ts = time.time()
    group_allocated = []
    commonl.progress(
        "allocation ID %s: [+%.1fs] keeping alive during state '%s'" % (
            allocid, ts - ts0, state))
    new_state = state		# in case we don't wait
    while wait_in_queue:
        if queue_timeout and ts - ts0 > queue_timeout:
            raise tc.blocked_e(
                "can't acquire targets, still busy after %ds"
                % queue_timeout, dict(targets = groups))
        time.sleep(keepalive_period)
        ts = time.time()
        state = data[allocid]
        try:
            r = rtb.send_request("PUT", "keepalive", json = data)
        except requests.exceptions.RequestException:
            # FIXME: tolerate N failures before giving up
            pass

        # COMPAT: old version packed the info in the 'result' field,
        # newer have it in the first level dictionary
        if 'result' in r:
            result = r.pop('result')
            r.update(result)
        # COMPAT: end        
        commonl.progress(
            "allocation ID %s: [+%.1fs] keeping alive during state '%s': %s"
            % (allocid, ts - ts0, state, r))

        if allocid not in r:
            continue # no news
        alloc = r[allocid]
        new_state = alloc['state']
        if new_state == 'active':
            r = rtb.send_request("GET", "allocation/%s" % allocid)
            group_allocated = r['group_allocated'].split(',')
            break
        elif new_state == 'invalid':
            print "\nallocation ID %s: [+%.1fs] now invalid" % (
                allocid, ts - ts0)
            break
        print "\nallocation ID %s: [+%.1fs] state transition %s -> %s" % (
            allocid, ts - ts0, state, new_state)
        data[allocid] = new_state
    return allocid, new_state, group_allocated


def _alloc_hold(rtb, allocid, state, ts0, max_hold_time):
    while True:
        time.sleep(2)
        ts = time.time()
        if max_hold_time > 0 and ts - ts0 > max_hold_time:
            # maximum hold time reached, release it
            break
        data = { allocid: state }
        r = rtb.send_request("PUT", "keepalive", json = data)

        # COMPAT: old version packed the info in the 'result' field,
        # newer have it in the first level dictionary
        if 'result' in r:
            result = r.pop('result')
            r.update(result)
        # COMPAT: end        
        commonl.progress(
            "allocation ID %s: [+%.1fs] keeping alive during state '%s': %s"
            % (allocid, ts - ts0, state, r))
        # r is a dict, allocids that changed state of the ones
        # we told it in 'data'
        ## { ALLOCID1: STATE1, ALLOCID2: STATE2 .. }
        new_data = r.get(allocid, None)
        if new_data == None:
            continue			# no new info
        new_state = new_data['state']
        if new_state not in ( 'active', 'queued', 'restart-needed' ):
            print	# to get a newline in
            break
        if new_state != data[allocid]:
            print "\nallocation ID %s: [+%.1fs] state transition %s -> %s" % (
                allocid, ts - ts0, state, new_state)
        state = new_state

def _cmdline_alloc_targets(args):
    with msgid_c("cmdline"):
        targetl = ttb_client.cmdline_list(args.target, args.all)
        if not targetl:
            logging.error("No targets could be used (missing? disabled?)")
            return
        targets = set()
        rtbs = set()

        # to use fullid, need to tweak the refresh code to add the aka part
        for rt in sorted(targetl, key = lambda x: x['fullid']):
            targets.add(rt['id'])
            rtbs.add(rt['rtb'])

        if len(rtbs) > 1:
            logging.error("Targets span more than one server: %s", rtbs)
            sys.exit(1)
        rtb = list(rtbs)[0]
        allocid = args.allocid
        try:
            groups = { "group": list(targets) }
            ts0 = time.time()
            if allocid == None:
                allocid, state, group_allocated = \
                    _alloc_targets(rtb, groups, obo = args.obo,
                                   preempt = args.preempt,
                                   queue = args.queue, priority = args.priority,
                                   reason = args.reason,
                                   wait_in_queue = args.wait_in_queue)
                ts = time.time()
                if args.wait_in_queue:
                    print "allocation ID %s: [+%.1fs] allocated: %s" % (
                        allocid, ts - ts0, " ".join(group_allocated))
                else:
                    print "allocation ID %s: [+%.1fs] registered" % (
                        allocid, ts - ts0)
            else:
                print "%s: NOT ALLOCATED! Holdin allocation ID given with -a" \
                    % allocid
                state = 'unknown'	# wild guess
                ts = time.time()
            if args.hold == None:	# user doesn't want us to ...
                return			# ... keepalive while active
            _alloc_hold(rtb, allocid, state, ts0, args.hold)
        except KeyboardInterrupt:
            ts = time.time()
            if allocid:
                print "\nallocation ID %s: [+%.1fs] releasing due to user interruption" % (
                    allocid, ts - ts0)
                _delete(rtb, allocid)


class _model_c(object):

    def __init__(self, servers, targets):
        self.targets = targets
        self.servers = servers
        self.max_waiters = 30

    def get_content(self):

        for rtb in self.servers:
            try:
                # FIXME: list only for a given set of targets
                r = rtb.send_request(
                    "GET", "targets/",
                    data = {
                        'projection': json.dumps([ "_alloc*" ])
                    })
                #print >> sys.stderr, "DEBUG refreshed", rtb, pprint.pformat(r)
                # update our knowledge of the target
                for rt in r.get('targets', []):
                    target_name = rt.get('id', None)
                    if target_name == None:
                        continue
                    if target_name not in self.targets:
                        # FIXME: use fullid instead
                        # FIXME: use rtb to compare too
                        continue
                    #print >> sys.stderr, "DEBUG", target_name, rt
                    self.targets[target_name].rt = rt
            except requests.exceptions.RequestException as e:
                # FIXME: set status bar "LOST CONNECTION"
                continue

        # return the content per rows
        l = []
        count = 0
        for target in self.targets.values():
            ## "_alloc_queue": [
            ##     {
            ##         "allocid": "PMAbeM",
            ##         "exclusive": true,
            ##         "preempt": false,
            ##         "priority": 50000,
            ##         "timestamp": 20200305204652
            ##     },
            ##     {
            ##         "allocid": "1KeyqK",
            ##         "exclusive": true,
            ##         "preempt": false,
            ##         "priority": 50000,
            ##         "timestamp": 20200305204654
            ##     }
            ## ],
            waiter_count = 0
            #print >> sys.stderr, "DEBUG target %s" % target.id, target.rt
            # ( prio, timestamp, allocid, preempt, exclusive)
            waiterl = []
            queue = target.rt.get('_alloc', {}).get('queue', {})
            for allocid, waiter in queue.iteritems():
                if waiter_count > self.max_waiters:
                    break
                waiter_count += 1
                bisect.insort(waiterl, (
                    waiter['priority'],
                    waiter['timestamp'],
                    allocid,
                    waiter['preempt'],
                    waiter['exclusive'],
                ))
            row = [ target.id ]
            for waiter in waiterl:
                row.append("%06d:%s" % (waiter[0], waiter[2]))
            l.append(( row, count ))
            count += 1
        return l

    def get_column_widths(self):
        return [ 14 ] * self.max_waiters


if asciimatics_support:
    class _view_c(asciimatics.widgets.Frame):
        # cannibalized top.py and contact_list.py from asciimatics's
        # samples to make this -- very helpful
        def __init__(self, screen, model):
            asciimatics.widgets.Frame.__init__(
                self, screen, screen.height, screen.width,
                hover_focus = True, has_border = True,
                can_scroll = True)
            self.model = model
            self.last_frame = 0

            layout = asciimatics.widgets.Layout([100], fill_frame=True)
            self.add_layout(layout)
            # Create the form for displaying the list of contacts.
            self.list_box = asciimatics.widgets.MultiColumnListBox(
                asciimatics.widgets.Widget.FILL_FRAME,
                model.get_column_widths(),
                model.get_content(),
                name = "Targets",
                add_scroll_bar = True)        
            layout.add_widget(self.list_box)
            self.fix()

        def process_event(self, event):
            if isinstance(event, asciimatics.event.KeyboardEvent):
                # key handling for this: Ctrl-C, q/Q to quit, r refresh
                if event.key_code in [
                        ord('q'),
                        ord('Q'),
                        asciimatics.screen.Screen.KEY_ESCAPE,
                        asciimatics.screen.Screen.ctrl("c")
                ]:
                    raise asciimatics.exceptions.StopApplication("User quit")
                elif event.key_code in [ ord("r"), ord("R") ]:
                    pass
                self.last_frame = 0	# force a refresh
            return asciimatics.widgets.Frame.process_event(self, event)

        @property
        def frame_update_count(self):
            return 10	        # Refresh once every .5 seconds by default.

        def _update(self, frame_no):
            if self.last_frame == 0 \
               or frame_no - self.last_frame >= self.frame_update_count:
                self.list_box.options = self.model.get_content()
                self.list_box.value = frame_no
                self.last_frame = frame_no
            asciimatics.widgets.Frame._update(self, frame_no)


def _cmdline_alloc_monitor(args):
    if asciimatics_support == False:
        raise RuntimeError(
            "asciimatics package needs to be installed for this feature; "
            "run 'pip install --user asciimatics' or equivalent")
    with msgid_c("cmdline"):
        servers = set()
        targetl = ttb_client.cmdline_list(args.target, args.all)
        targets = collections.OrderedDict()

        # to use fullid, need to tweak the refresh code to add the aka part
        for rt in sorted(targetl, key = lambda x: x['id']):
            target_name = rt['id']
            targets[target_name] = \
                tc.target_c.create_from_cmdline_args(
                    # load no extensions, not needed, plus faster
                    args, target_name, extensions_only = [])
            servers.add(targets[target_name].rtb)
        model = _model_c(servers, targets)

        def _run_alloc_monitor(screen, scene):
            scenes = [
                asciimatics.scene.Scene([ _view_c(screen, model) ],
                                        -1, name = "Main"),
            ]
            
            screen.play(scenes,
                        stop_on_resize = True, start_scene = scene,
                        allow_int = True)

        last_scene = None
        while True:
            try:
                asciimatics.screen.Screen.wrapper(_run_alloc_monitor,
                                                  catch_interrupt = True,
                                                  arguments = [ last_scene ])
                sys.exit(0)
            except asciimatics.exceptions.ResizeScreenError as e:
                last_scene = e.scene


def _allocs_get(rtb, username):
    try:
        r = rtb.send_request("GET", "allocation/")
    except (Exception, ttb_client.requests.HTTPError) as e:
        logging.error("%s", e)
        return {}
    if username:
        # filter here, as we can translate the username 'self' to the
        # user we are logged in as in the server
        _r = {}
        if username == "self":
            username = rtb.logged_in_username()

        def _alloc_filter(allocdata, username):
            if username != None \
               and username != allocdata.get('creator', None) \
               and username != allocdata.get('user', None):
                return False
            return True

        for allocid, allocdata in r.items():
            if _alloc_filter(allocdata, username):
                _r[allocid] = allocdata

        return _r
    else:
        return r


def _alloc_ls(verbosity, username = None):
    allocs = {}
    tp = ttb_client._multiprocessing_pool_c(
        processes = len(ttb_client.rest_target_brokers))
    threads = {}
    for rtb in sorted(ttb_client.rest_target_brokers.itervalues()):
        threads[rtb] = tp.apply_async(_allocs_get, (rtb, username))
    tp.close()
    tp.join()
    for rtb, thread in threads.iteritems():
        allocs[rtb.aka] = thread.get()

    if verbosity < 0:
        # just print the list of alloc ids for each server, one per line
        for _, data in allocs.iteritems():
            if data:
                print "\n".join(data.keys())
        return
    elif verbosity == 3:
        pprint.pprint(allocs)
        return
    elif verbosity == 4:
        print json.dumps(allocs, skipkeys = True, indent = 4)
        return

    table = []
    for rtb, r in allocs.iteritems():
        for allocid, data in r.iteritems():
            userl = []
            user = data.get('user', None)
            creator = data['creator']
            guests = data.get('guests', [])
            if 'priority' in data:
                prio = str(data['priority'])
                if data['preempt']:
                    prio += ":P"
            else:
                prio = "n/a"
            userl = [ user ]
            if user != creator:
                userl.append(creator + " (creator)")
            for guest in guests:
                userl.append(guest + " (guest)")
            if verbosity == 0:
                table.append([
                    allocid,
                    # put state/prio/preempt together
                    data['state'] + " " + prio,
                    "\n".join(userl),
                    len(data.get('target_group', [])),
                    data.get('reason', "n/a"),
                ])
            elif verbosity == 1:
                tgs = []
                for name, group in data.get('target_group', {}).iteritems():
                    tgs.append( name + ": " + ",".join(group))
                table.append([
                    allocid,
                    rtb,
                    data['state'],
                    prio,
                    data.get('timestamp', 'n/a'),
                    "\n".join(userl),
                    "\n".join(tgs),
                    data.get('reason', "n/a"),
                ])
            elif verbosity == 2:
                commonl.data_dump_recursive(data, allocid,)
    if verbosity == 0:
        headers0 = [
            "AllocID",
            "State",
            "Users",
            "#Groups",
            "Reason"
        ]
        print(tabulate.tabulate(table, headers = headers0))
    if verbosity == 1:
        headers1 = [
            "AllocID",
            "Server",
            "State",
            "Priority",
            "Timestamp",
            "Users",
            "Groups",
            "Reason",
        ]
        print(tabulate.tabulate(table, headers = headers1))

def _cmdline_alloc_ls(args):
    with msgid_c("cmdline"):
        targetl = ttb_client.cmdline_list(args.target, args.all)
        targets = collections.OrderedDict()

        if not ttb_client.rest_target_brokers:
            logging.error("E: no servers available, did you configure?")
            return

        # to use fullid, need to tweak the refresh code to add the aka part
        for rt in sorted(targetl, key = lambda x: x['fullid']):
            target_name = rt['fullid']
            targets[target_name] = \
                tc.target_c.create_from_cmdline_args(
                    # load no extensions, not needed, plus faster
                    args, target_name, extensions_only = [])

        if args.refresh:
            print "\x1b[2J"	# clear whole screen
            print "\x1b[1;1H"	# move to column 1,1
            sys.stdout.flush()
            clear = True
            ts0 = time.time()
            while True:
                try:
                    if clear:
                        print "\x1b[2J"	# clear whole screen
                        clear = False
                    _alloc_ls(args.verbosity - args.quietosity, args.username)
                    ts0 = time.time()
                except requests.exceptions.RequestException as e:
                    ts = time.time()
                    print "[LOST CONNECTION +%ds]: %s" % (ts - ts0, e)
                    clear = True

                print "\x1b[0J"	# clean what is left
                print "\x1b[1;1H"	# move to column 1,1
                sys.stdout.flush()
                time.sleep(args.refresh)
        else:
            _alloc_ls(args.verbosity - args.quietosity, args.username)

def _cmdline_alloc_delete(args):
    with msgid_c("cmdline"):

        # we don't know which request is on which server, so we send
        # it to all the servers
        def _allocid_delete(allocid):

            try:
                rtb = None
                if '/' in allocid:
                    server_aka, allocid = allocid.split('/', 1)
                    for rtb in ttb_client.rest_target_brokers.values():
                        if rtb.aka == server_aka:
                            rtb = rtb
                            _delete(rtb, allocid)
                            return
                    else:
                        logging.error("%s: unknown server name", server_aka)
                        return
                # Unknown server, so let's try them all ... yeah,
                # collateral damage might happen--but then, you can
                # only delete yours
                for rtb in ttb_client.rest_target_brokers.values():
                    _delete(rtb, allocid)
            except Exception as e:
                logging.exception("Exception: %s", e)

        tp = ttb_client._multiprocessing_pool_c(
            processes = len(args.allocid))
        threads = {}
        for allocid in args.allocid:
            threads[allocid] = tp.apply_async(_allocid_delete,
                                                   (allocid,))
        tp.close()
        tp.join()

def _rtb_allocid_extract(allocid):
    rtb = None
    if '/' in allocid:
        server_aka, allocid = allocid.split('/', 1)
        for rtb in ttb_client.rest_target_brokers.values():
            if rtb.aka == server_aka:
                return rtb, allocid
        logging.error("%s: unknown server name", server_aka)
        return None, allocid
    return None, allocid

def _guests_add(rtb, allocid, guests):
    for guest in guests:
        try:
            rtb.send_request("PATCH", "allocation/%s/%s"
                             % (allocid, guest))
        except requests.HTTPError as e:
            logging.warning("%s: can't add guest %s: %s",
                            allocid, guest, e)


def _guests_list(rtb, allocid):
    r = rtb.send_request("GET", "allocation/%s" % allocid)
    print "\n".join(r.get('guests', []))

def _guests_remove(rtb, allocid, guests):
    if not guests:
        # no guests given, remove'em all -- so list them first
        r = rtb.send_request("GET", "allocation/%s" % allocid)
        guests = r.get('guests', [])
    for guest in guests:
        try:
            r = rtb.send_request("DELETE", "allocation/%s/%s"
                                 % (allocid, guest))
        except requests.HTTPError as e:
            logging.error("%s: can't remove guest %s: %s",
                          allocid, guest, e)


def _cmdline_guest_add(args):
    with msgid_c("cmdline"):
        rtb, allocid = _rtb_allocid_extract(args.allocid)
        if rtb == None:
            # Unknown server, so let's try them all ... yeah,
            # collateral damage might happen--but then, you can
            # only delete yours
            for rtb in ttb_client.rest_target_brokers.values():
                _guests_add(rtb, allocid, args.guests)
        else:
            _guests_add(rtb, allocid, args.guests)



def _cmdline_guest_list(args):
    with msgid_c("cmdline"):
        rtb, allocid = _rtb_allocid_extract(args.allocid)
        if rtb == None:
            # Unknown server, so let's try them all ... yeah,
            # collateral damage might happen--but then, you can
            # only delete yours
            for rtb in ttb_client.rest_target_brokers.values():
                _guests_list(rtb, allocid)
        else:
            _guests_list(rtb, allocid)



def _cmdline_guest_remove(args):
    with msgid_c("cmdline"):
        rtb, allocid = _rtb_allocid_extract(args.allocid)
        if rtb == None:
            # Unknown server, so let's try them all ... yeah,
            # collateral damage might happen--but then, you can
            # only delete yours
            for rtb in ttb_client.rest_target_brokers.values():
                _guests_remove(rtb, allocid, args.guests)
        else:
            _guests_remove(rtb, allocid, args.guests)

def _cmdline_setup(arg_subparsers):
    ap = arg_subparsers.add_parser(
        "alloc-targets",
        help = "Allocate targets for exclusive use")
    commonl.argparser_add_aka(arg_subparsers, "alloc-targets", "acquire")
    ap.add_argument(
        "-a", "--all", action = "store_true", default = False,
        help = "Consider also disabled targets")
    ap.add_argument(
        "-r", "--reason", action = "store",
        # use instead of getfqdn(), since it does a DNS lookup and can
        # slow things a lot
        default = "cmdline %s@%s:%d" % (
            getpass.getuser(), socket.gethostname(), os.getppid()),
        help = "Reason to pass to the server (default: %(default)s)"
        " [LOGNAME:HOSTNAME:PARENTPID]")
    ap.add_argument(
        "--hold", action = "store_const",
        const = 0, dest = "hold", default = None,
        help = "Keep the reservation alive until cancelled with Ctrl-C")
    ap.add_argument(
        "-d", "--hold-for", dest = "hold", action = "store",
        nargs = "?", type = int, default = None,
        help = "Keep the reservation alive for this many seconds, "
        "then release it")
    ap.add_argument(
        "-w", "--wait", action = "store_true", dest = 'queue', default = True,
        help = "(default) Wait until targets are assigned")
    ap.add_argument(
        "--dont-wait", action = "store_false", dest = 'wait_in_queue',
        default = True,
        help = "Do not wait until targets are assigned")
    ap.add_argument(
        "-i", "--inmediate", action = "store_false", dest = 'queue',
        help = "Fail if target's can't be allocated inmediately")
    ap.add_argument(
        "-p", "--priority", action = "store", type = int, default = 500,
        help = "Priority (0 highest, 999 lowest)")
    ap.add_argument(
        "-o", "--obo", action = "store", default = None,
        help = "User to alloc on behalf of")
    ap.add_argument(
        "--preempt", action = "store_true", default = False,
        help = "Enable preemption (disabled by default)")
    ap.add_argument(
        "target", metavar = "TARGETSPEC", nargs = "+",
        action = "store", default = None,
        help = "Target's names, all in the same server")
    ap.set_defaults(func = _cmdline_alloc_targets)

    ap = arg_subparsers.add_parser(
        "alloc-monitor",
        help = "Monitor the allocations current in the system")
    ap.add_argument(
        "-a", "--all", action = "store_true", default = False,
        help = "Consider also disabled targets")
    ap.add_argument(
        "target", metavar = "TARGETSPEC", nargs = "*",
        action = "store", default = None,
        help = "Target's names or a general target specification "
        "which might include values of tags, etc, in single quotes (eg: "
        "'zephyr_board and not type:\"^qemu.*\"'")
    ap.set_defaults(func = _cmdline_alloc_monitor)

    ap = arg_subparsers.add_parser(
        "alloc-ls",
        help = "List information about current allocations "
        "in all the servers or the servers where the named "
        "targets are")
    commonl.argparser_add_aka(arg_subparsers, "alloc-ls", "alloc-list")
    ap.add_argument(
        "-q", dest = "quietosity", action = "count", default = 0,
        help = "Decrease verbosity of information to display "
        "(none is a table, -q or more just the list of allocations,"
        " one per line")
    ap.add_argument(
        "-v", dest = "verbosity", action = "count", default = 0,
        help = "Increase verbosity of information to display "
        "(none is a table, -v table with more details, "
        "-vv hierarchical, -vvv Python format, -vvvv JSON format)")
    ap.add_argument(
        "-a", "--all", action = "store_true", default = False,
        help = "Consider also disabled targets")
    ap.add_argument(
        "-u", "--username", action = "store", default = None,
        help = "ID of user whose allocs are to be displayed"
        " (optional, defaults to anyone visible)")
    ap.add_argument(
        "-r", "--refresh", action = "store",
        type = float, nargs = "?", const = 1, default = 0,
        help = "Repeat every int seconds (by default, only once)")
    ap.add_argument(
        "target", metavar = "TARGETSPEC", nargs = "*",
        action = "store", default = None,
        help = "Target's names or a general target specification "
        "which might include values of tags, etc, in single quotes (eg: "
        "'zephyr_board and not type:\"^qemu.*\"'")
    ap.set_defaults(func = _cmdline_alloc_ls)

    ap = arg_subparsers.add_parser(
        "alloc-rm",
        help = "Delete an existing allocation (which might be "
        "in any state; any targets allocated to said allocation "
        "will be released")
    commonl.argparser_add_aka(arg_subparsers, "alloc-rm", "alloc-del")
    commonl.argparser_add_aka(arg_subparsers, "alloc-rm", "alloc-delete")
    ap.add_argument(
        "allocid", metavar = "[SERVER/]ALLOCATIONID", nargs = "+",
        action = "store", default = None,
        help = "Allocation IDs to remove")
    ap.set_defaults(func = _cmdline_alloc_delete)

    ap = arg_subparsers.add_parser(
        "guest-add",
        help = "Add a guest to an allocation")
    ap.add_argument(
        "allocid", metavar = "[SERVER/]ALLOCATIONID",
        action = "store", default = None,
        help = "Allocation IDs to which to add guest to")
    ap.add_argument(
        "guests", metavar = "USERNAME", nargs = "+",
        action = "store", default = None,
        help = "Name of guest to add")
    ap.set_defaults(func = _cmdline_guest_add)

    ap = arg_subparsers.add_parser(
        "guest-ls",
        help = "list guests in an allocation")
    ap.add_argument(
        "allocid", metavar = "[SERVER/]ALLOCATIONID",
        action = "store", default = None,
        help = "Allocation IDs to which to add guest to")
    ap.set_defaults(func = _cmdline_guest_list)

    ap = arg_subparsers.add_parser(
        "guest-rm",
        help = "Remove a guest from an allocation")
    commonl.argparser_add_aka(arg_subparsers, "guest-rm", "guest-remove")
    ap.add_argument(
        "allocid", metavar = "[SERVER/]ALLOCATIONID",
        action = "store", default = None,
        help = "Allocation IDs to which to add guest to")
    ap.add_argument(
        "guests", metavar = "USERNAME", nargs = "*",
        action = "store", default = None,
        help = "Name of guest to remove (all if none given)")
    ap.set_defaults(func = _cmdline_guest_remove)
