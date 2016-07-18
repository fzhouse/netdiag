#!/usr/bin/python

import csv
import xlwt
import xlrd
import time
import shlex
import os
import platform
import logging
import shortuuid
import json
import subprocess
import paramiko
import getpass

test_duration = 10
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

class Node():
    def __init__(self, address, name=None):
        self.address = address
        if name:
            self.name = name
        self.name = address

class Host(Node):
    def __init__(self, address, name=None, ssh_address=None, ssh_port=22, username='root', password=None, keyfile=None):
        Node.__init__(self, address, name)
        if self.address == '127.0.0.1':
            self.username = getpass.getuser()
        if ssh_address:
            self.ssh_address = ssh_address
        else:
            self.ssh_address = address
        self.ssh_port = ssh_port
        self.username = username
        self.password = password
        self.keyfile = keyfile
        self.connect()
    
    def connect(self):
        if self.address == '127.0.0.1':
            logger.info("host %s is local" % self.name)
            return
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            if self.keyfile:
                logger.info("login with key %s" % self.keyfile)
                ssh.connect(self.ssh_address, port=self.ssh_port, username=self.username, key_filename=self.keyfile)
            elif self.password:
                logger.info("login with password %s" % self.password)
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
        if self.address == '127.0.0.1':
            p = subprocess.Popen(shlex.split(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
            stdout, stderr = p.communicate()
            return stdout
        try:
            logger.info('[%s@%s] %s' % (self.username, self.address, cmd))
            stdin, stdout, stderr = self.ssh.exec_command(cmd)
            out = stdout.readlines()
            return out
        except Exception, e:
            logger.error("run %s error: %s", (cmd, e))

    def make_scripts(self, cmds):
        logger.info(cmds)
        cmds.append("rm -- \"$0\"")
        br = '\n'
        cmd = br.join(cmds)
        fi = open("./run.sh", "w")
        fi.write(cmd)
        fi.close()
        self.put_file("./run.sh", "/tmp/run.sh")
        os.system("rm -rf ./run.sh")

    def exec_commands(self, cmds):
        self.make_scripts(cmds)
        return self.exec_command("/bin/bash /tmp/run.sh")

    def exec_command_bg(self, cmd, log=None):
        if log:
            log = '/tmp/%s' % log
        else:
            log = '/dev/null'
        cmds = ["nohup %s &> %s &" % (cmd, log), "echo $!"]
        pid = int(self.exec_commands(cmds)[0], 10)
        logger.info("%s pid: %s" % (cmd, pid))
        return pid

    def exec_commands_bg(self, cmds, log):
        self.make_scripts(cmds)
        self.exec_command_bg("/bin/bash /tmp/run.sh", log)

    def kill_pid(self, pid):
        self.exec_command("kill -2 %d" % pid)

    def get_file(self, remotepath, localdir="."):
        filename = os.path.basename(remotepath)
        localpath = "%s/%s_%s" % (localdir, self.address, filename)
        if self.address == '127.0.0.1':
            p = subprocess.Popen(shlex.split('cp %s %s' % (remotepath, localpath)), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
            p.wait()
            logger.info("[%s@%s] put %s to %s" % (self.username, self.address, localpath, remotepath))
            return
        try:
            sftp = self.ssh.open_sftp()
            sftp.get(remotepath, localpath)
            sftp.close()
            logger.info("[%s@%s] get %s from %s" % (self.username, self.address, localpath, remotepath))
        except e:
            logger.error("get file error: %s" % e)

    def put_file(self, localpath, remotepath):
        if self.address == '127.0.0.1':
            p = subprocess.Popen(shlex.split('cp %s %s' % (localpath, remotepath)), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
            p.wait()
            logger.info("[%s@%s] put %s to %s" % (self.username, self.address, localpath, remotepath))
            return
        try:
            sftp = self.ssh.open_sftp()
            sftp.put(localpath, remotepath)
            logger.info("[%s@%s] put %s to %s" % (self.username, self.address, localpath, remotepath))
        except Exception, e:
            logger.error("put file error: %s" % e)

    def wait_pid(self, pid):
        while 1:
            out = self.exec_command("ps -q %d" % pid)
            if len(out) == 2:
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
        if str(self.dst.__class__) == '__main__.Node':
            self.diag_simple()
            return
        self.diag_complex()



if __name__ == '__main__':
    h1 = DiagHost('10.0.63.202', 'h1', '10.0.63.202', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'], iperf_port=5001)
    h2 = Node('10.0.63.203', 'h2')
    h3 = DiagHost('127.0.0.1', 'h3')

    diag = Diagnostics(h1, h2)
    diag.run()

    diag = Diagnostics(h3, h1)
    diag.run()
