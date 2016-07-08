#!/usr/bin/python

import multiprocessing
import datetime
import paramiko
import time
import shortuuid
import os
import logging
import xlwt
import csv

test_duration = 10
test_bandwidth = '1M'
mtr_int = 0.2
mtr_count = int(test_duration/2/mtr_int)

logger = logging.getLogger('netdiag')
logger.setLevel(logging.INFO)
hdr = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)-15s] %(filename)s %(levelname)-8s %(message)s')
hdr.setFormatter(formatter)
logger.addHandler(hdr)
#logging.basicConfig(level=logging.INFO, format=FORMAT, filename='diag.log', filemode='w')

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


class Host:
    def __init__(self, name, address):
        self.name = name
        self.address = address


class ManagedHost(Host):
    def __init__(self, name, address, ssh_address=None, ssh_port=22, username='root', password=None, keyfile=None):
        Host.__init__(self, name, address)
        if ssh_address:
            self.ssh_address = ssh_address
        else:
            self.ssh_address = address
        self.ssh_port = ssh_port
        self.username = username
        self.password = password
        self.keyfile = keyfile

    def connect(self):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            if self.keyfile:
                ssh.connect(self.ssh_address, port=self.ssh_port, username=self.username, key_filename=self.keyfile)
            elif self.password:
                ssh.connect(self.ssh_address, port=self.ssh_port, username=self.username, password=self.password)
            else:
                ssh.connect(self.ssh_address, port=self.ssh_port, username=self.username, key_filename='%s/.ssh/id_rsa' % os.environ['HOME'])
            return ssh
        except e:
            logger.error(e)
            return None

    def exec_command(self, cmd):
        try:
            ssh = self.connect()
            logger.info('[%s@%s] %s' % (self.username, self.address, cmd))
            stdin, stdout, stderr = ssh.exec_command(cmd)
            out = stdout.readlines()
            ssh.close()
            return out
        except e:
            logger.error(e)
            return None

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

    def exec_command_bg(self, cmd, log):
        cmds = ["nohup %s &> /tmp/%s &" % (cmd, log), "echo $!"]
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
        ssh = self.connect()
        if ssh:
            sftp = ssh.open_sftp()
            sftp.get(remotepath, localpath)
            sftp.close()
            logger.info("[%s@%s] get %s from %s" % (self.username, self.address, localpath, remotepath))
        ssh.close()

    def put_file(self, localpath, remotepath):
        ssh = self.connect()
        if ssh:
            sftp = ssh.open_sftp()
            sftp.put(localpath, remotepath)
            sftp.close()
            logger.info("[%s@%s] put %s to %s" % (self.username, self.address, localpath, remotepath))
        ssh.close()

    def wait_pid(self, pid):
        while 1:
            out = self.exec_command("ps -q %d" % pid)
            if len(out) == 2:
                time.sleep(10)
            else:
                return

class DiagHost(ManagedHost):
    def __init__(self, name, address, ssh_address, ssh_port=22, username='root', password='', keyfile=None, iperf_port=5001):
        ManagedHost.__init__(self, name, address, ssh_address, ssh_port, username, password, keyfile)
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

    def run_traceroute(self, remote):
        hops = []
        cmd = "traceroute -q 1 -n -N 30 %s" % remote.address
        ssh = self.connect()
        if ssh:
            stdin, stdout, stderr = ssh.exec_command("echo \"[%s@%s] %s\"" % (self.username, self.address, cmd))
            print stdout.readlines()[0]
            stdin, stdout, stderr = ssh.exec_command(cmd)
            lines = stdout.readlines()
            for line in lines:
                if line.startswith('traceroute to'):
                    continue
                info = line.split()
                if len(info) == 2:
                    continue
                hops.append(info[1])
            ssh.close()
        return hops

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

    def run(self):
        if str(self.dst.__class__) == '__main__.Host':
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
            return

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


if __name__ == '__main__':
    rwanda = DiagHost('rwanda', '10.0.63.202', '10.0.63.202', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'], iperf_port=5001)
    ireland = Host('ireland', '10.0.63.203')
    #ireland = DiagHost('ireland', '10.0.63.203', '10.0.63.203', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'], iperf_port=5001)
    singapore = DiagHost('singapore', '10.0.63.204', '10.0.63.204', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'], iperf_port=5001)

    diag = Diagnostics(rwanda, ireland)
    diag.run()

    diag = Diagnostics(singapore, rwanda)
    diag.run()
