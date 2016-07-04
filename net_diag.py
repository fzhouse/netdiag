#!/usr/bin/python

import multiprocessing
import datetime
import paramiko
import time
import uuid
import os
import string

test_duration = 10
test_bandwidth = '1M'
mtr_count = 100

class Host:
    def __init__(self, name, address, port=22, username='root', password='', keyfile=None):
        self.name = name
        self.address = address
        self.port = port
        self.username = username
        self.password = password
        self.keyfile = keyfile

    def connect(self):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if self.keyfile:
            ssh.connect(self.address, port=self.port, username=self.username, key_filename=self.keyfile)
        elif self.password:
            ssh.connect(self.address, port=self.port, username=self.username, password=self.password)
        else:
            print 'Can not connect to host %s' % self.address
            return None
        return ssh

    def exec_command(self, cmd):
        cur_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ssh = self.connect()
        if ssh:
            stdin, stdout, stderr = ssh.exec_command("echo \"%s [%s@%s] %s\"" % (cur_time, self.username, self.address, cmd))
            print stdout.readlines()[0].strip()
            stdin, stdout, stderr = ssh.exec_command(cmd)
            out = stdout.readlines()
        ssh.close()
        return out

    def make_scripts(self, cmds):
        print cmds
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
        pid = string.atoi(self.exec_commands(cmds)[0], 10)
        print "pid: %s" % pid
        return pid

    def exec_commands_bg(self, cmds, log):
        self.make_scripts(cmds)
        self.exec_command_bg("/bin/bash /tmp/run.sh", log)

    def kill_pid(self, pid):
        self.exec_command("kill -2 %d" % pid)

    def get_file(self, remotepath, localdir="."):
        cur_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filename = os.path.basename(remotepath)
        localpath = "%s/%s_%s" % (localdir, self.address, filename)
        ssh = self.connect()
        if ssh:
            sftp = ssh.open_sftp()
            sftp.get(remotepath, localpath)
            sftp.close()
            os.system("echo \"%s [%s@%s]\" get %s from %s" % (cur_time, self.username, self.address, localpath, remotepath))
        ssh.close()

    def put_file(self, localpath, remotepath):
        cur_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ssh = self.connect()
        if ssh:
            sftp = ssh.open_sftp()
            sftp.put(localpath, remotepath)
            sftp.close()
            os.system("echo \"%s [%s@%s]\" put %s to %s" % (cur_time, self.username, self.address, localpath, remotepath))
        ssh.close()

    def wait_pid(self, pid):
        while 1:
            out = self.exec_command("ps -q %d" % pid)
            if len(out) == 2:
                time.sleep(10)
            else:
                return

class DiagHost(Host):
    def __init__(self, name, address, port=22, username='root', password='', keyfile=None, test_address=None, iperf_port=5001):
        Host.__init__(self, name, address, port, username, password, keyfile)
        if test_address == None:
            self.test_address = address
        else:
            self.test_address = test_address
        self.iperf_port = iperf_port

    def run_iperf_server(self, log):
        cmd = "iperf -s -u -i 1 -p %d" % self.iperf_port
        return self.exec_command_bg(cmd, log)

    def run_iperf_client(self, remote, log):
        cmd = "iperf -c %s -u -b %s -t %d -i 1 -p %d" % (remote.address, test_bandwidth, test_duration, remote.iperf_port)
        return self.exec_command_bg(cmd, log)

    def run_ping(self, remote, log):
        cmd = "ping -A %s" % remote.address
        return self.exec_command_bg(cmd, log)

    def run_mtr(self, remote, log):
        cmd = "mtr -r -n -c %d -i 0.2 %s" % (mtr_count, remote.address)
        return self.exec_command_bg(cmd, log)

    def run_sar(self, log):
        cmd = "sar -n DEV 1"
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

    def kill_sar(self):
        cmd = "killall -2 sar"
        self.exec_command(cmd)

    def kill_ping(self):
        cmd = "killall -2 ping"
        self.exec_command(cmd)

    def rm_file(self, path):
        cmd = "rm -rf %s" % path
        self.exec_command(cmd)

    def clear_procs(self):
        cmds = ["killall -2 iperf", "killall -2 sar", "killall -2 ping", "rm -rf /tmp/*.log"]
        self.exec_commands(cmds)

    def clear_logs(self, tid):
        self.exec_command("rm -rf /tmp/*%s.log" % tid)


class Diagnostics:
    def __init__(self, src, dst):
        self.src = src
        self.dst = dst
        self.tid = uuid.uuid1()
        self.src_logs = []
        self.dst_logs = []

    def run(self):
        self.src.clear_procs()
        self.dst.clear_procs()

        iperf_server_log = "iperf_server_%s.log" % self.tid
        self.dst.run_iperf_server(iperf_server_log)
        self.dst_logs.append(iperf_server_log)
        iperf_client_log = "iperf_client_%s.log" % self.tid
        self.src.run_iperf_client(self.dst, iperf_client_log)
        self.src_logs.append(iperf_client_log)

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
        self.dst.kill_sar()
        self.src.kill_sar()

        for log in self.src_logs:
            self.src.get_file("/tmp/%s" % log)
        for log in self.dst_logs:
            self.dst.get_file("/tmp/%s" % log)

        self.src.clear_logs(self.tid)
        self.dst.clear_logs(self.tid)


if __name__ == '__main__':
    rwanda = DiagHost('rwanda', '10.0.63.202', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'], test_address='10.0.63.202', iperf_port=5001)
    ireland = DiagHost('ireland', '10.0.63.203', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'], test_address='10.0.63.203', iperf_port=5001)
    singapore = DiagHost('singapore', '10.0.63.204', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'], test_address='10.0.63.203', iperf_port=5001)

    diag = Diagnostics(rwanda, ireland)
    diag.run()

