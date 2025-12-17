#!/usr/bin/python3
import docker
import argparse
import shutil
import signal
import time
import sys
import os

label_name = "hoster.domains"
enclosing_pattern = "#-----------Docker-Hoster-Domains----------\n"
hosts_path = "/tmp/hosts"
hosts = {}

def signal_handler(signal, frame):
    global hosts
    hosts = {}
    update_hosts_file()
    sys.exit(0)

def main():
    # register the exit signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    args = parse_args()
    global hosts_path
    hosts_path = args.file
    
    dockerClient = docker.APIClient(base_url='unix://%s' % args.socket)
    events = dockerClient.events(decode=True)

    # get running containers
    for c in dockerClient.containers(quiet=True, all=False):
        container_id = c["Id"]
        container = get_container_data(dockerClient, container_id)
        hosts[container_id] = container
    update_hosts_file()

    # listen for events to keep the hosts file updated
    for e in events:
        if e["Type"] != "container":
            continue
        status = e["status"]
        container_id = e["id"]
        if status == "start":
            container = get_container_data(dockerClient, container_id)
            hosts[container_id] = container
            update_hosts_file()
        elif status in ["stop", "die", "destroy"]:
            if container_id in hosts:
                hosts.pop(container_id)
            update_hosts_file()
        elif status == "rename":
            if container_id in hosts:
                container = get_container_data(dockerClient, container_id)
                hosts[container_id] = container
            update_hosts_file()

def get_container_data(dockerClient, container_id):
    """
    Return relevant container data including IP(s) and domains.
    Safely handles modern Docker network structures.
    """
    info = dockerClient.inspect_container(container_id)
    container_hostname = info["Config"]["Hostname"]
    container_name = info["Name"].strip("/")

    # Network dictionary (may contain multiple networks)
    networks = info["NetworkSettings"].get("Networks", {})
    result = []

    # Iterate through networks
    for net_name, net_data in networks.items():
        ip = net_data.get("IPAddress")
        if not ip:
            continue  # skip networks without IP

        # Build list of domains
        domains = [container_name]
        if container_hostname:
            full_host = container_hostname
            if info["Config"].get("Domainname"):
                full_host += "." + info["Config"]["Domainname"]
            domains.append(full_host)
        if net_data.get("Aliases"):
            domains += net_data["Aliases"]

        # Remove duplicates
        domains = list(dict.fromkeys(domains))

        result.append({
            "ip": ip,
            "name": container_name,
            "domains": domains
        })

    return result

def update_hosts_file():
    if len(hosts) == 0:
        print("Removing all hosts before exit...")
    else:
        print("Updating hosts file with:")
        for id, addresses in hosts.items():
            for addr in addresses:
                print("ip: %s domains: %s" % (addr["ip"], addr["domains"]))

    # read all the lines of the original file
    lines = []
    with open(hosts_path, "r+") as hosts_file:
        lines = hosts_file.readlines()

    # remove all the lines after the known pattern
    for i, line in enumerate(lines):
        if line == enclosing_pattern:
            lines = lines[:i]
            break

    # remove all trailing empty lines
    if lines:
        while lines[-1].strip() == "":
            lines.pop()

    # append all the domain lines
    if len(hosts) > 0:
        lines.append("\n\n" + enclosing_pattern)
        for id, addresses in hosts.items():
            for addr in addresses:
                lines.append("%s %s\n" % (addr["ip"], " ".join(addr["domains"])))
        lines.append("#-----Do-not-add-hosts-after-this-line-----\n\n")

    # write it to an auxiliary file first
    aux_file_path = hosts_path + ".aux"
    with open(aux_file_path, "w") as aux_hosts:
        aux_hosts.writelines(lines)

    # replace the hosts file atomically
    shutil.move(aux_file_path, hosts_path)

def parse_args():
    parser = argparse.ArgumentParser(description='Synchronize running docker container IPs with host /etc/hosts file.')
    parser.add_argument('socket', type=str, nargs="?", default="tmp/docker.sock",
                        help='The docker socket to listen for docker events.')
    parser.add_argument('file', type=str, nargs="?", default="/tmp/hosts",
                        help='The /etc/hosts file to sync the containers with.')
    return parser.parse_args()

if __name__ == '__main__':
    main()
