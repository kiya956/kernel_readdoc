# dns_resolver — Kernel DNS Resolution Subsystem

## Overview

The kernel DNS resolver provides an in-kernel mechanism for resolving hostnames
to IP addresses, primarily serving the CIFS and AFS filesystem modules. Rather
than implementing a full DNS stack in the kernel, it leverages the `request_key`
interface to perform userspace upcalls, delegating actual DNS resolution to a
userspace helper program (`key.dns_resolver`).

When a kernel subsystem (such as CIFS or AFS) needs to resolve a hostname, it
calls into the dns_resolver module, which creates a key request. The kernel
keyring subsystem then invokes the userspace `key.dns_resolver` binary, which
performs the DNS lookup and returns the result as a key payload. This design
keeps DNS complexity out of the kernel while providing a clean, cacheable
interface for name resolution.

**Source:** `net/dns_resolver/`

**Kernel config:** `CONFIG_DNS_RESOLVER=y/m`

## How It Works

1. A kernel client (CIFS, AFS) calls `dns_query()` or
   `dns_resolve_server_name_to_ip()` with a hostname.
2. The dns_resolver module constructs a key description and calls
   `request_key()` with key type `dns_resolver`.
3. The keyring subsystem checks for a cached key. If none is found, it
   performs a userspace upcall to `/sbin/key.dns_resolver`.
4. The userspace helper resolves the hostname (via `/etc/resolv.conf`,
   DNS servers, etc.) and instantiates the key with the resolved IP address
   as its payload.
5. The resolved address is returned to the caller. The key remains cached
   in the keyring for its TTL, avoiding repeated upcalls.

### Key Type Registration

The module registers the `dns_resolver` key type with the following operations:

| Operation   | Function                  | Description                        |
|-------------|---------------------------|------------------------------------|
| match       | `dns_resolver_match`      | Match keys by description          |
| describe    | `dns_resolver_describe`   | Describe key for /proc/keys output |
| preparse    | `dns_resolver_preparse`   | Pre-parse key payload              |
| free_preparse | `dns_resolver_free_preparse` | Free pre-parsed data          |
| instantiate | `generic_key_instantiate` | Instantiate key with payload       |
| revoke      | `dns_resolver_revoke`     | Revoke a DNS key                   |
| destroy     | `dns_resolver_destroy`    | Destroy a DNS key                  |

## Key Functions

### `dns_query()`

```c
int dns_query(struct net *net,
              const char *type,
              const char *name, size_t namelen,
              const char *options,
              char **_result, time64_t *_expiry,
              bool invalidate);
```

Primary interface for DNS resolution. Constructs a key description from the
query type and name, then calls `request_key()` to obtain the result. The
`type` parameter specifies the DNS record type (e.g., `"a"` for A records,
`"afsdb"` for AFSDB records). Returns the resolved address string in
`_result`.

### `dns_resolve_server_name_to_ip()`

```c
int dns_resolve_server_name_to_ip(const char *unc,
                                  unsigned int ip_addr,
                                  struct sockaddr *addr);
```

Higher-level wrapper used primarily by CIFS. Takes a UNC server name and
resolves it to a `sockaddr` structure suitable for socket connections. Handles
both IPv4 and IPv6 address parsing from the DNS result.

## Source Files

| File                          | Purpose                                |
|-------------------------------|----------------------------------------|
| `net/dns_resolver/dns_key.c`  | Key type definition, match/describe ops|
| `net/dns_resolver/dns_query.c`| `dns_query()` implementation           |
| `net/dns_resolver/Kconfig`    | Build configuration                    |
| `net/dns_resolver/Makefile`   | Build rules                            |
| `include/keys/dns_resolver-type.h` | Key type header                  |

## Integration with CIFS

The CIFS (SMB) filesystem uses `dns_resolve_server_name_to_ip()` to resolve
server names from UNC paths (e.g., `\\server\share`). This is invoked during
mount operations and DFS (Distributed File System) referral chasing. The
`CONFIG_CIFS` option selects `CONFIG_DNS_RESOLVER` as a dependency.

Key call path:
```
cifs_mount() → cifs_resolve_server() → dns_resolve_server_name_to_ip()
    → dns_query() → request_key("dns_resolver", ...) → userspace upcall
```

## Integration with AFS

The AFS filesystem uses `dns_query()` directly with type `"afsdb"` to locate
AFS database servers (via AFSDB DNS records) and with type `"a"` for standard
hostname resolution. The AFS client calls this during cell configuration and
volume location.

Key call path:
```
afs_vl_lookup_vldb() → dns_query(net, "afsdb", cell_name, ...)
    → request_key("dns_resolver", ...) → userspace upcall
```

## Tracing and Debugging

### Check Module Status

```bash
# Verify module is loaded
lsmod | grep dns_resolver

# Check registered key type
grep dns_resolver /proc/keys 2>/dev/null
cat /proc/key-users

# View exported symbols
grep dns /proc/kallsyms | grep -i resolv
```

### bpftrace Examples

#### Trace dns_query() Calls

```
sudo bpftrace -e '
kprobe:dns_query {
    printf("dns_query: type=%s name=%s\n",
           str(arg1), str(arg2));
}
kretprobe:dns_query {
    printf("dns_query returned: %d\n", retval);
}'
```

#### Trace request_key Upcalls for DNS

```
sudo bpftrace -e '
kprobe:request_key {
    $type = str(arg0);
    if ($type == "dns_resolver") {
        printf("request_key: type=%s desc=%s\n",
               $type, str(arg1));
    }
}'
```

#### Trace dns_resolver Key Operations

```
sudo bpftrace -e '
kprobe:dns_resolver_match {
    printf("dns_resolver_match called\n");
}
kprobe:dns_resolver_describe {
    printf("dns_resolver_describe called\n");
}'
```

#### Trace CIFS DNS Resolution

```
sudo bpftrace -e '
kprobe:dns_resolve_server_name_to_ip {
    printf("CIFS DNS resolve: unc=%s\n", str(arg0));
}
kretprobe:dns_resolve_server_name_to_ip {
    printf("CIFS DNS resolve returned: %d\n", retval);
}'
```

### Trigger DNS Resolution for Testing

```bash
# Mount a CIFS share to trigger DNS resolution
mount -t cifs //server.example.com/share /mnt -o user=test

# Use keyctl to inspect cached DNS keys
keyctl show @s
keyctl list @s
```

## References

- `Documentation/networking/dns_resolver.rst` — Kernel documentation
- `net/dns_resolver/` — Source code
- `key.dns_resolver(8)` — Userspace helper man page
- `request-key(8)` — Generic key request upcall mechanism
