# Setup PopUp-Server Network #

Guide to configure the network of the raspberry pi acting as popup server.

## Dual network configuration ##
To configure two networks through ethernet, one dynamic (DHCP) and another one static, edit the file `/etc/network/interfaces` and add the following lines

```bash

# DHCP Network
auto eth0
allow-hotplug eth0
iface eth0 inet dhcp

# Static IP 
auto eth0:0
iface eth0:0 inet static
address 192.168.1.100  # <-- add static IP here
netmask 255.255.255.0
```

## NTP Server ##
To configure a custom Network Time Protocol (NTP) server, open the file: `/etc/systemd/timesyncd.conf` and add your server at NTP:

```bash
#  This file is part of systemd.
#
#  systemd is free software; you can redistribute it and/or modify it under the
#  terms of the GNU Lesser General Public License as published by the Free
#  Software Foundation; either version 2.1 of the License, or (at your option)
#  any later version.
#
# Entries in this file show the compile time defaults. Local configuration
# should be created by either modifying this file, or by creating "drop-ins" in
# the timesyncd.conf.d/ subdirectory. The latter is generally recommended.
# Defaults can be restored by simply deleting this file and all drop-ins.
#
# See timesyncd.conf(5) for details.

[Time]
NTP=192.168.1.200  # <-- Your NTP server here
#FallbackNTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org 2.debian.pool.ntp.org 3.debian.pool.ntp.org
#RootDistanceMaxSec=5
#PollIntervalMinSec=32

```
