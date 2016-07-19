#!/usr/bin/python

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

NUL_DEV = {
    'Linux':    '/dev/null',
    'Windows':  'NUL',
}

TMP_DIR = {
    'Linux':    '/tmp',
    'Windows':  '%TEMP%',
}

SELFDEL_CMD = {
    'Linux':    'rm -- "$0"',
    'Windows':  '(goto) 2>NUL % del "%~f0"',
}

RUN_SCRIPT = {
    'Linux':    'run.sh',
    'Windows':  'run.bat',
}

RUN_CMD = {
    'Linux':    '/bin/bash',
    'Windows':  'call',
}

test_duration = 5
test_bandwidth = '1M'
mtr_int = 0.2
mtr_count = int(test_duration/2/mtr_int)

logger = logging.getLogger('netdiag')
logger.setLevel(logging.DEBUG)
hdr = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)-15s] %(filename)s %(levelname)-8s %(message)s')
hdr.setFormatter(formatter)
logger.addHandler(hdr)

def csv2xlsx(xlsx, log):
    data = os.path.basename(log).split('_')
    sheetname = "%s_%s" % (data[0], data[1])
    sheet = xlsx.add_sheet(sheetname)
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
    logger.info("save %s to %s" % (log, sheetname))
    os.system("rm -rf %s" % log)

def run_aux(cmd, log, q):
    f = open(log, 'w')
    p = subprocess.Popen(cmd, stdout=f, universal_newlines=True, shell=True)
    q.put(p.pid)
    ret = p.wait()
    f.flush()
    f.close()


class Node():
    def __init__(self, address, name=None):
        self.address = address
        if name:
            self.name = name
        self.name = address


class Host(Node):
    def __init__(self, address, name=None, ssh_address=None, ssh_port=22, username='root', password=None, keyfile=None):
        Node.__init__(self, address, name)
        if ssh_address:
            self.ssh_address = ssh_address
        else:
            self.ssh_address = address
        self.ssh_port = ssh_port
        self.username = username
        self.password = password
        self.keyfile = keyfile
        if self.address == '127.0.0.1':
            self.username = getpass.getuser()
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
                ssh.connect(self.ssh_address, port=self.ssh_port, username=self.username, key_filename='%s/.ssh/id_rsa' % os.environ['HOME'])
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
        logger.info("make %s: %s" % (RUN_SCRIPT[self.system], cmds))
        cmds.append(SELFDEL_CMD[self.system])
        br = '\n'
        cmd = br.join(cmds)
        fi = open("./%s" % RUN_SCRIPT[self.system], "w")
        fi.write(cmd)
        fi.close()
        self.put_file("./" + RUN_SCRIPT[self.system], "%s/%s" % (TMP_DIR[self.system], RUN_SCRIPT[self.system]))
        os.remove("./%s" % RUN_SCRIPT[self.system])

    def exec_commands(self, cmds):
        self.make_scripts(cmds)
        return self.exec_command("%s %s/%s" % (RUN_CMD[self.system], TMP_DIR[self.system], RUN_SCRIPT[self.system]))

    def exec_command_bg(self, cmd, log=None):
        if log:
            log = '%s/%s' % (TMP_DIR[self.system], log)
        else:
            log = NUL_DEV[self.system]
        if self.system == 'Linux':
            cmds = ["nohup %s &> %s &" % (cmd, log), "echo $!"]
            pid = int(self.exec_commands(cmds)[0], 10)
        else:
            # On Windows, cmd can only be run locally
            q = multiprocessing.Queue()
            p = multiprocessing.Process(target=run_aux, args=(cmd, log, q))
            p.start()
            pid = q.get()
        logger.info("[%s@%s] %s with pid: %s" % (self.username, self.name, cmd, pid))
        return pid

    def exec_commands_bg(self, cmds, log):
        self.make_scripts(cmds)
        self.exec_command_bg("%s %s/%s" % (RUN_CMD[self.system], TMP_DIR[self.system], RUN_SCRIPT[self.system]), log)

    def kill_pid(self, pid):
        if self.system == 'Linux':
            self.exec_command("kill -2 %d" % pid)
        if self.system == 'Windows':
            self.exec_command("taskkill /F /T /PID %i" % pid)

    def get_file(self, remotepath, localdir="."):
        filename = os.path.basename(remotepath)
        localpath = "%s/%s_%s" % (localdir, self.address, filename)
        if self.address == '127.0.0.1':
            p = subprocess.Popen('cp %s %s' % (remotepath, localpath), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
            p.wait()
            logger.info("from %s@%s:%s get %s" % (self.username, self.name, remotepath, localpath))
            return
        try:
            sftp = self.ssh.open_sftp()
            sftp.get(remotepath, localpath)
            sftp.close()
            logger.info("from %s@%s:%s get %s" % (self.username, self.name, remotepath, localpath))
        except Exception, e:
            logger.error("from %s@%s:%s get %s error: %s" % (self.username, self.name, remotepath, localpath, e))

    def put_file(self, localpath, remotepath):
        if self.address == '127.0.0.1':
            p = subprocess.Popen('cp %s %s' % (localpath, remotepath), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
            p.wait()
            logger.info("put %s to %s@%s:%s" % (localpath, self.username, self.name, remotepath))
            return
        try:
            sftp = self.ssh.open_sftp()
            sftp.put(localpath, remotepath)
            logger.info("put %s to %s@%s:%s" % (localpath, self.username, self.name, remotepath))
        except Exception, e:
            logger.error("put %s to %s@%s:%s error: %s" % (localpath, self.username, self.name, remotepath, e))

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


class DiagHost(Host):
    def __init__(self, address, name=None, ssh_address=None, ssh_port=22, username='root', password='', keyfile=None, iperf_port=5001):
        Host.__init__(self, address, name, ssh_address, ssh_port, username, password, keyfile)
        self.iperf_port = iperf_port

    def run_iperf_server(self, log):
        cmd = "iperf -s -u -i 1 -p %d -y C" % self.iperf_port
        return self.exec_command_bg(cmd, log)

    def run_iperf_client(self, remote, log):
        cmd = "iperf -c %s -u -d -b %s -t %d -i 1 -p %d -y C" % (remote.address, test_bandwidth, test_duration, remote.iperf_port)
        return self.exec_command_bg(cmd, log)

    def run_ping(self, remote, log):
        cmd = "ping -A %s" % remote.address
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
        cmds = ["killall -2 sadc", "mv /tmp/%s /tmp/tmplog" % log, "sadf -d /tmp/tmplog -- -r -n DEV | grep -v '^#' | sed 's/;/,/g' > /tmp/%s" % log, "rm -rf /tmp/tmplog"]
        self.exec_commands(cmds)

    def kill_ping(self):
        cmd = "killall -2 ping"
        self.exec_command(cmd)

    def rm_file(self, path):
        cmd = "rm -rf %s" % path
        self.exec_command(cmd)

    def clear_procs(self):
        cmds = ["killall -2 iperf", "killall -2 sadc", "killall -2 ping", "rm -rf /tmp/*.log"]
        self.exec_commands(cmds)

    def clear_logs(self, tid):
        self.exec_command("rm -rf /tmp/*%s.log" % tid)


class Diagnostics:
    def __init__(self, src, dst):
        self.src = src
        self.dst = dst
        self.tid = shortuuid.uuid()
        self.src_logs = []
        self.dst_logs = []

    def diag_local_windows(self):
        pass

    def diag_simple(self):
        self.src.clear_procs()
        mtr_log = "mtr_%s.log" % self.tid
        mtr_pid = self.src.run_mtr(self.dst, mtr_log)
        time.sleep(test_duration/2)
        self.src.wait_pid(mtr_pid)
        self.src.get_file("/tmp/%s" % mtr_log)
        xlsx = xlwt.Workbook()
        csv2xlsx(xlsx, "%s_%s" % (self.src.address, mtr_log))
        xlsx.save("%s.xlsx" % self.tid)
        self.src.clear_logs(self.tid)

    def diag_complex(self):
        self.src.clear_procs()
        self.dst.clear_procs()

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

        xlsx = xlwt.Workbook()
        for log in self.src_logs:
            self.src.get_file("/tmp/%s" % log)
            csv2xlsx(xlsx, "%s_%s" % (self.src.address, log))
        for log in self.dst_logs:
            self.dst.get_file("/tmp/%s" % log)
            csv2xlsx(xlsx, "%s_%s" % (self.dst.address, log))
        xlsx.save("%s.xlsx" % self.tid)

        self.src.clear_logs(self.tid)
        self.dst.clear_logs(self.tid)

    def run(self):
        logger.info("================ %s --> %s ================" % (self.src.name, self.dst.name))
        if str(self.dst.__class__) == '__main__.Node':
            self.diag_simple()
            return
        self.diag_complex()



if __name__ == '__main__':
    h1 = DiagHost('10.0.63.202', 'h1', '10.0.63.202', 22, 'root', password='startimes123!@#', iperf_port=5001)
    h2 = DiagHost('10.0.63.204', 'h2', password='startimes123!@#', iperf_port=5001)
    h3 = Node('10.0.63.203', 'h3')
    h4 = DiagHost('127.0.0.1', 'h4')

    diag = Diagnostics(h1, h2)
    diag.run()
    
    diag = Diagnostics(h1, h3)
    diag.run()

    diag = Diagnostics(h4, h1)
    diag.run()
