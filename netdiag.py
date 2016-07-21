#!/usr/bin/python
# -*- coding: UTF-8 -*- 

import httplib
import multiprocessing
import csv
import xlwt
import xlrd
import time
import os
import platform
import logging
import shortuuid
import json
import subprocess
import paramiko
import getpass
import shutil
import tempfile
import netaddr

NUL_DEV = {
    'Linux':    '/dev/null',
    'Windows':  'NUL',
}
TMP_DIR = {
    'Linux':    '/tmp/',
    # Windows system can only be localhost
    'Windows':  tempfile.gettempdir() + '\\',
}
SELFDEL_CMD = {
    'Linux':    'rm -- "$0"',
    'Windows':  '(goto) 2>NUL & del "%~f0"',
}
SCRIPT_EXT = {
    'Linux':    '.sh',
    'Windows':  '.bat',
}
RUN_CMD = {
    'Linux':    '/bin/bash',
    'Windows':  'call',
}
WRT_FLAG = {
    'Linux':    'wb',
    'Windows':  'w',
}

test_duration = 5
test_bandwidth = '1M'
mtr_int = 0.2
mtr_count = int(test_duration/2/mtr_int)
ping_count = int(test_duration)

logger = logging.getLogger('netdiag')
logger.setLevel(logging.DEBUG)
hdr = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)-15s] %(filename)s %(levelname)-8s %(message)s')
hdr.setFormatter(formatter)
logger.addHandler(hdr)

def csv2xlsx(xlsx, log):
    try:
        data = os.path.basename(log).split('_')
        sheetname = "%s_%s" % (data[0], data[1])
        sheet = xlsx.add_sheet(sheetname)
    except Exception, e:
        logger.error("save %s to xlsx error: %s" % (log, e))
        return
    try:
        csvfile = open(log, "rb")
        reader = csv.reader(csvfile)
        l = 0
        for line in reader:
            if line[0].startswith('#'):
                continue
            r = 0
            for i in line:
                sheet.write(l, r, i)
                r += 1
            l += 1
    except Exception, e:
        logger.error("write %s to xlsx error: %s" % (log, e))
    csvfile.close()
    logger.info("save %s to %s" % (log, sheetname))
    os.remove(log)

def run_aux(cmd, log, q):
    try:
        if log:
            f = open(log, 'wb')
        else:
            f = open(os.devnull, 'wb')
        p = subprocess.Popen(cmd, stdout=f, universal_newlines=True, shell=True)
        q.put(p.pid)
        ret = p.wait()
        f.flush()
        f.close()
    except Exception, e:
        logger.error('run %s error: %s' % (cmd, e))


class Node():
    def __init__(self, address, name=None):
        self.address = address
        if name:
            self.name = name
        else:
            if self.address == '127.0.0.1':
                self.name = 'localhost'
            else:
                self.name = address


class Host(Node):
    def __init__(self, address, name=None, ssh_address=None, ssh_port=22, username='root', password=None, keyfile=None):
        Node.__init__(self, address, name)
        if ssh_address:
            self.ssh_address = ssh_address
        else:
            self.ssh_address = address
        self.ssh_port = ssh_port
        if self.address == '127.0.0.1':
            self.username = getpass.getuser()
        else:
            self.username = username
        self.password = password
        self.keyfile = keyfile
        if self.address == '127.0.0.1':
            self.system = platform.system()
        else:
            self.system = 'Linux'
        self.connect()

    def connect(self):
        if self.address == '127.0.0.1':
            logger.info("host %s is local" % self.name)
            return
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            if self.keyfile:
                logger.info("login %s@%s with key %s" % (self.username, self.name, self.keyfile))
                ssh.connect(self.ssh_address, port=self.ssh_port, username=self.username, key_filename=self.keyfile)
            elif self.password:
                logger.info("login %s@%s with password %s" % (self.username, self.name, self.password))
                ssh.connect(self.ssh_address, port=self.ssh_port, username=self.username, password=self.password)
            else:
                ssh.connect(self.ssh_address, port=self.ssh_port, username=self.username, key_filename='%s/.ssh/id_rsa' % os.path.expanduser('~'))
            self.ssh = ssh
        except Exception, e:
            logger.error("connect %s error: %s" % (self.name, e))

    def __del__(self):
        self.disconnect()

    def disconnect(self):
        if self.address == '127.0.0.1':
            return
        self.ssh.close()

    def exec_command(self, cmd):
        logger.info('[%s@%s] %s' % (self.username, self.name, cmd))
        try:
            if self.address == '127.0.0.1':
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
                stdout, stderr = p.communicate()
                return stdout
            else:
                stdin, stdout, stderr = self.ssh.exec_command(cmd)
                return stdout.readlines()
        except Exception, e:
            logger.error("run %s error: %s" % (cmd, e))

    def make_scripts(self, cmds):
        try:
            sid = shortuuid.uuid()
            cmds.append(SELFDEL_CMD[self.system])
            run_script = 'run_' + sid + SCRIPT_EXT[self.system]
            logger.info("make %s from %s" % (run_script, cmds))
            br = '\n'
            cmd = br.join(cmds)
            fi = open(run_script, WRT_FLAG[self.system])
            fi.write(cmd)
            fi.close()
            script_remote = TMP_DIR[self.system] + run_script
            self.put_file(run_script, script_remote)
            os.remove(run_script)
            return script_remote
        except Exception, e:
            logger.error("make script error: %s" % e)

    def exec_commands(self, cmds):
        run_script = self.make_scripts(cmds)
        return self.exec_command(RUN_CMD[self.system] + ' ' + run_script)

    def exec_command_bg(self, cmd, log=None):
        try:
            if self.system == 'Linux':
                if not log:
                    log = '/dev/null'
                cmds = ["nohup %s &> /tmp/%s &" % (cmd, log), "echo $!"]
                pid = int(self.exec_commands(cmds)[0], 10)
            else:
                q = multiprocessing.Queue()
                p = multiprocessing.Process(target=run_aux, args=(cmd, TMP_DIR[self.system] + log, q))
                p.start()
                pid = q.get()
            logger.info("[%s@%s] %s with pid: %s" % (self.username, self.name, cmd, pid))
            return pid
        except Exception, e:
            logger.error('run background %s error: %s' % (cmds, e))

    def exec_commands_bg(self, cmds, log):
        run_script = self.make_scripts(cmds)
        return self.exec_command_bg(RUN_CMD[self.system] + ' ' + TMP_DIR[self.system] + run_script, log)

    def kill_pid(self, pid):
        if self.system == 'Linux':
            self.exec_command("kill -2 %d" % pid)
        if self.system == 'Windows':
            self.exec_command("taskkill /F /T /PID %i" % pid)

    def wait_pid(self, pid):
        while 1:
            if self.system == 'Linux':
                out = self.exec_command("ps -q %i" % pid)
                if len(out) == 2:
                    time.sleep(10)
                else:
                    return
            if self.system == 'Windows':
                out = self.exec_command('tasklist /fi "PID eq %i"' % pid)
                if len(out) == 5:
                    time.sleep(10)
                else:
                    return

    def get_file(self, remotepath, localdir=''):
        localpath = localdir + "%s_%s" % (self.address, os.path.basename(remotepath))
        try:
            if self.address == '127.0.0.1':
                shutil.copyfile(remotepath, localpath)
            else:
                sftp = self.ssh.open_sftp()
                sftp.get(remotepath, localpath)
                sftp.close()
            logger.info("from %s@%s:%s to %s" % (self.username, self.name, remotepath, localpath))
        except Exception, e:
            logger.error("from %s@%s:%s get %s error: %s" % (self.username, self.name, remotepath, localpath, e))

    def put_file(self, localpath, remotepath):
        try:
            if self.address == '127.0.0.1':
                shutil.copyfile(localpath, remotepath)
            else:
                sftp = self.ssh.open_sftp()
                sftp.put(localpath, remotepath)
                sftp.close()
            logger.info("from %s to %s@%s:%s" % (localpath, self.username, self.name, remotepath))
        except Exception, e:
            logger.error("put %s to %s@%s:%s error: %s" % (localpath, self.username, self.name, remotepath, e))


class DiagHost(Host):
    def __init__(self, address, name=None, ssh_address=None, ssh_port=22, username='root', password='', keyfile=None, iperf_port=5001):
        Host.__init__(self, address, name, ssh_address, ssh_port, username, password, keyfile)
        self.iperf_port = iperf_port
        if self.system == 'Windows':
            self.code = self.chcp()

    def chcp(self):
        if self.system == 'Windows':
            out = self.exec_command('chcp')
            info = out.split()
            code = info[len(info)-1] 
            if code == '437': 
                lang = 'English' 
            elif code == '936': 
                lang = 'Chinese' 
            else: 
                lang = 'Others' 
            logger.info('Your system language is ' + lang) 
            return code
    
    def run_ping(self, remote, log):
        if self.system == 'Windows':
            cmd = 'ping -n %d %s' % (ping_count, remote.address)
            fi = open(TMP_DIR[self.system]+log, 'wb') 
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, shell=True) 
            seq = 1 
            while True: 
                out = p.stdout.readline() 
                if out == '': 
                    if p.poll is not None: 
                        break 
                else: 
                    if self.code == '437': 
                        hit_str = 'Relay from' 
                        miss_str = 'Request timed out' 
                    elif self.code == '936': 
                        hit_str = u'\u6765\u81ea' 
                        miss_str = u'\u8bf7\u6c42\u8d85\u65f6'.encode('gbk') 
                    if out.startswith(hit_str): 
                        data = '%d,' % seq 
                        outs = out.split() 
                        byts = outs[3].split('=', 1)[1] 
                        delay = outs[4].split('=', 1)[1].split('ms', 1)[0] 
                        ttl = outs[5].split('=', 1)[1] 
                        data += '%s,%s,%s' % (byts, delay, ttl) 
                        seq += 1 
                    elif out.startswith(miss_str): 
                        data = '%d,' % seq 
                        data += '0,-1,0' 
                        seq += 1 
                    else: 
                        continue 
                    fi.write(data + '\n') 
                    fi.flush() 
            fi.close()
        if self.system == 'Linux':
            cmd = "ping -A %s" % remote.address
            return self.exec_command_bg(cmd, log)

    def run_tracert(self, remote, log):
        if self.system == 'Windows':
            cmd = 'tracert -d -h 64 %s' % remote.address 
            fi = open(TMP_DIR[self.system]+log, 'wb') 
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, shell=True) 
            while True: 
                out = p.stdout.readline() 
                if out == '': 
                    if p.poll is not None: 
                        break 
                else: 
                    outs = out.split() 
                    if len(outs) == 0: 
                        continue 
                    if not outs[0].isdigit(): 
                        continue 
                    data = outs[0] 
                    start = 1 
                    for i in range(3): 
                        if outs[start] == '*': 
                            data += ',-1' 
                            start += 1 
                        else: 
                            if outs[start] == '<1': 
                                data += ',0' 
                            else: 
                                data += ',' + outs[start] 
                            start += 2 
                    addr = outs[start] 
                    if self.code == '437': 
                        miss_str = 'Request timed out' 
                    elif self.code == '936': 
                        miss_str = u'\u8bf7\u6c42\u8d85\u65f6'.encode('gbk') 
                    if addr.startswith(miss_str): 
                        addr = '0.0.0.0' 
                    data += ',' + addr
                    fi.write(data + '\n') 
                    fi.flush() 
            fi.close() 

    def run_iperf_server(self, log):
        cmd = "iperf -s -u -i 1 -p %d -y C" % self.iperf_port
        return self.exec_command_bg(cmd, log)

    def run_iperf_client(self, remote, log):
        cmd = "iperf -c %s -u -d -b %s -t %d -i 1 -p %d -y C" % (remote.address, test_bandwidth, test_duration, remote.iperf_port)
        return self.exec_command_bg(cmd, log)

    def run_mtr(self, remote, log):
        cmd = "mtr -r -n -C -c %d -i %.1f %s | sed 's/;/,/g'" % (mtr_count, mtr_int, remote.address)
        return self.exec_command_bg(cmd, log)

    def run_sar(self, log):
        cmd = "/usr/lib64/sa/sadc 1 14400"
        return self.exec_command_bg(cmd, log)

    def kill_iperf(self):
        cmd = "killall -2 iperf"
        self.exec_command(cmd)

    def kill_sar(self, log):
        cmds = ["killall -9 sadc", "mv /tmp/%s /tmp/tmplog" % log, "sadf -d /tmp/tmplog -- -r -n DEV | grep -v '^#' | sed 's/;/,/g' > /tmp/%s" % log, "rm -rf /tmp/tmplog"]
        self.exec_commands(cmds)

    def kill_ping(self):
        cmd = "killall -2 ping"
        self.exec_command(cmd)

    def rm_file(self, path):
        cmd = "rm -rf %s" % path
        self.exec_command(cmd)

    def clear_procs(self):
        if self.system == 'Linux':
            cmds = ["killall -2 iperf", "killall -9 sadc", "killall -2 ping", "rm -rf /tmp/*.log"]
        if self.system == 'Windows':
            cmds = ["taskkill /im ping.exe /f", "taskkill /im iperf.exe /f", "del %s*.log" % TMP_DIR[self.system]]
        self.exec_commands(cmds)

    def clear_logs(self, tid):
        if self.system == 'Linux':
            self.exec_command("rm -rf /tmp/*%s.log" % tid)
        if self.system == 'Windows':
            self.exec_command("del %s*%s.log" % (TMP_DIR[self.system], tid))

    def get_base_info(self, log, host=None):
        try:
            if self.address == '127.0.0.1' or (host and is_internal_ip(host.address)):
                if host:
                    logpath = "%s_%s" % (host.address, log)
                    if is_internal_ip(host.address):
                        url = 'http://ipinfo.io'
                    else:
                        url = 'http://ipinfo.io/' + host.address
                else:
                    logpath = "%s_%s" % (self.address, log)
                    url = 'http://ipinfo.io'
                cli = httplib.HTTPConnection('ipinfo.io', 80, timeout=30)
                cli.request('GET', url)
                res = cli.getresponse()
                data = res.read()
                logger.info(data)
                ipinfo = json.loads(data)
            else:
                if host:
                    url = 'http://ipinfo.io/' + host.address
                    logpath = "%s_%s" % (host.address, log)
                else:
                    url = 'http://ipinfo.io'
                    logpath = "%s_%s" % (self.address, log)
                ipinfo = json.loads('\n'.join(self.exec_command('curl "%s"' % url)))
        except Exception, e:
            logger.error("get ip info error: %s" % e)
        try:
            fi = open(logpath, 'wb')
            basestr = "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s" % (self.name, self.address, self.system, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), ipinfo['ip'], ipinfo['country'], ipinfo['region'], ipinfo['city'], ipinfo['org'], ipinfo['loc'])
            fi.write(basestr)
            logger.info("write base info: %s to %s" % (basestr, log))
        except Exception, e:
            logger.error("write base info error: %s" % e)
        fi.close()

class Diagnostics:
    def __init__(self, src, dst):
        self.src = src
        self.dst = dst
        self.tid = shortuuid.uuid()
        self.src_logs = []
        self.dst_logs = []

    def logs_to_xlsx(self):
        xlsx = xlwt.Workbook()
        csv2xlsx(xlsx, '%s_base_%s.log' % (self.src.address, self.tid))
        csv2xlsx(xlsx, '%s_base_%s.log' % (self.dst.address, self.tid))
        for log in self.src_logs:
            self.src.get_file(TMP_DIR[self.src.system] + log)
            csv2xlsx(xlsx, "%s_%s" % (self.src.address, log))
        for log in self.dst_logs:
            self.dst.get_file(TMP_DIR[self.dst.system] + log)
            csv2xlsx(xlsx, "%s_%s" % (self.dst.address, log))
        xlsx.save("%s.xlsx" % self.tid)

    def diag_base(self):
        base_log = "base_%s.log" % self.tid
        self.src.get_base_info(base_log)

        base_log = "base_%s.log" % self.tid
        if str(self.dst.__class__) == '__main__.Node':
            self.src.get_base_info(base_log, self.dst)
        else:
            self.dst.get_base_info(base_log)

    def diag_simple(self):
        self.src.clear_procs()

        self.diag_base()

        mtr_log = "mtr_%s.log" % self.tid
        mtr_pid = self.src.run_mtr(self.dst, mtr_log)
        time.sleep(test_duration/2)
        self.src.wait_pid(mtr_pid)
        self.src_logs.append(mtr_log)

        self.logs_to_xlsx()

        self.src.clear_logs(self.tid)

    def diag_simple_windows(self):
        self.src.clear_procs()

        self.diag_base()

        tr_log = "tracert_%s.log" % self.tid
        self.src.run_tracert(self.dst, tr_log)
        self.src_logs.append(tr_log)

        self.logs_to_xlsx()

        self.src.clear_logs(self.tid)

    def diag_complex(self):
        self.src.clear_procs()
        self.dst.clear_procs()

        self.diag_base()

        iperfserver_log = "iperfserver_%s.log" % self.tid
        self.dst.run_iperf_server(iperfserver_log)
        self.dst_logs.append(iperfserver_log)
        iperfclient_log = "iperfclient_%s.log" % self.tid
        self.src.run_iperf_client(self.dst, iperfclient_log)
        self.src_logs.append(iperfclient_log)

        sar_log = "sar_%s.log" % self.tid
        self.dst.run_sar(sar_log)
        self.dst_logs.append(sar_log)
        self.src.run_sar(sar_log)
        self.src_logs.append(sar_log)

        mtr_log = "mtr_%s.log" % self.tid
        mtr_pid = self.src.run_mtr(self.dst, mtr_log)
        self.src_logs.append(mtr_log)

        time.sleep(test_duration+1)
        self.src.wait_pid(mtr_pid)

        self.dst.kill_iperf()
        self.dst.kill_sar(sar_log)
        self.src.kill_sar(sar_log)

        self.logs_to_xlsx()

        self.src.clear_logs(self.tid)
        self.dst.clear_logs(self.tid)

    def diag_complex_windows(self):
        self.diag_simple_windows()

    def run(self):
        logger.info("================ %s --> %s ================" % (self.src.name, self.dst.name))
        if str(self.dst.__class__) == '__main__.Node':
            if self.src.system == 'Windows':
                self.diag_simple_windows()
            if self.src.system == 'Linux':
                self.diag_simple()
            return
        if self.src.system == 'Windows':
            self.diag_complex_windows()
        if self.src.system == 'Linux':
            self.diag_complex()


def is_internal_ip(ip):
    ipdec = int(netaddr.IPAddress(ip))
    lip1 = int(netaddr.IPAddress('127.0.0.0'))
    lip2 = int(netaddr.IPAddress('10.0.0.0'))
    lip3 = int(netaddr.IPAddress('172.16.0.0'))
    lip4 = int(netaddr.IPAddress('192.168.0.0'))
    if (ipdec >> 24) == (lip1 >> 24) or (ipdec >> 24) == (lip2 >> 24) or (ipdec >> 12) == (lip3 >> 12) or (ipdec >> 16) == (lip4 >> 16):
        return True
    return False


if __name__ == '__main__':
    h1 = DiagHost(address='10.0.63.202', username='root', password='startimes123!@#')
    h2 = DiagHost('10.0.63.204', username='root', password='startimes123!@#')
    h3 = Node('114.114.114.114')
    h4 = DiagHost('127.0.0.1')
    h5 = Node('10.0.63.2')

    diag = Diagnostics(h1, h2)
    diag.run()
    
    diag = Diagnostics(h1, h3)
    diag.run()

    diag = Diagnostics(h4, h1)
    diag.run()

    diag = Diagnostics(h4, h3)
    diag.run()

    diag = Diagnostics(h4, h5)
    diag.run()

    diag = Diagnostics(h1, h5)
    diag.run()
