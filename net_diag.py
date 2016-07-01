#!/usr/bin/python

import multiprocessing
import paramiko
import time
import uuid
import os

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

    def run_command(self, cmd):
        ssh = self.connect()
        if ssh:
            stdin, stdout, stderr = ssh.exec_command("echo \"[%s@%s] %s\"" % (self.username, self.address, cmd))
            print stdout.readlines()[0]
            stdin, stdout, stderr = ssh.exec_command(cmd)
            ssh.close()
        return stdout.readlines()

    def run_command_background(self, cmd, log):
        ssh = self.connect()
        if ssh:
            stdin, stdout, stderr = ssh.exec_command("echo '[%s@%s] %s'" % (self.username, self.address, cmd))
            print stdout.readlines()[0]
            stdin, stdout, stderr = ssh.exec_command("nohup %s &> %s &" % (cmd, log))
        ssh.close()

    def run_iperf_server(self):
        cmd = "iperf -s -u -i 1 -p 5002"
        log = "/tmp/iperf_%s.log" % uuid.uuid1()
        self.run_command_background(cmd, log)
        return log

    def run_iperf_client(self, remote):
        cmd = "iperf -c %s -u -b 1M -t 300 -i 1 -p 5002" % remote.address
        log = "/tmp/iperf_%s.log" % uuid.uuid1()
        self.run_command_background(cmd, log)
        return log

    def kill_iperf(self):
        cmd = "killall -2 iperf"
        self.run_command(cmd)

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

    def run_ping(self, remote):
        cmd = "ping -A %s" % remote.address
        log = "/tmp/ping_%s_%s.log" % (hop, uuid.uuid1())
        self.run_command_background(cmd, log)
        return log

    def kill_ping(self):
        cmd = "killall -2 ping"
        self.run_command(cmd)

    def get_file(self, path, filename):
        localpath = './%s_%s' % (self.name, filename)
        ssh = self.connect()
        if ssh:
            sftp = ssh.open_sftp()
            sftp.get(path, localpath)
            sftp.close()
        ssh.close()

    def put_file(self, localpath, remotepath):
        ssh = self.connect()
        if ssh:
            sftp = ssh.open_sftp()
            sftp.put(localpath, remotepath)
            sftp.close()
        ssh.close()

    def rm_file(self, path):
        cmd = "rm -rf %s" % path
        self.run_command(cmd)

    def run_mtr(self, remote):
        cmd = "mtr -r -n -c 1000 -i 0.2 %s" % remote.address
        log = "/tmp/mtr_%s.log" % uuid.uuid1()
        self.run_command_background(cmd, log)
        return log

if __name__ == '__main__':
    rwanda = Host('rwanda', '10.0.63.202', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'])
    ireland = Host('ireland', '10.0.63.203', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'])
    singapore = Host('singapore', '10.0.63.204', 22, 'root', keyfile='%s/.ssh/id_rsa' % os.environ['HOME'])

    ireland.rm_file("/tmp/*.log")
    singapore.rm_file("/tmp/*.log")
    rwanda.rm_file("/tmp/*.log")
    ireland.kill_iperf()
    singapore.kill_iperf()
    rwanda.kill_iperf()
    ireland.kill_ping()
    rwanda.kill_ping()
    singapore.kill_ping()

    iperf_server_log = ireland.run_iperf_server()
    iperf_client_log = rwanda.run_iperf_client(ireland)
    mtr_log = rwanda.run_mtr(ireland)
    time.sleep(300)
    ireland.kill_iperf()
    ireland.get_file(iperf_server_log, "iperf_server.log")
    rwanda.get_file(iperf_client_log, "iperf_client.log")
    rwanda.get_file(mtr_log, "mtr.log")

    iperf_server_log = rwanda.run_iperf_server()
    iperf_client_log = singapore.run_iperf_client(rwanda)
    time.sleep(300)
    rwanda.kill_iperf()
    rwanda.get_file(iperf_server_log, "iperf_server.log")
    singapore.get_file(iperf_client_log, "iperf_client.log")
