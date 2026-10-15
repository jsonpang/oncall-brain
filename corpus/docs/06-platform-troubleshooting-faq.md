# AetherPulse Technical Troubleshooting & Operational Runbook FAQ

## 1. Git Repository & Commit Remediation
### Q: How to reset local commits to match remote tracking branch while preserving uncommitted work?
**Runbook Solution**:
To synchronize working trees with remote branch state while retaining local changes in the staging index:
```bash
git fetch origin
git reset --soft origin/main
```
If you need to replace a commit message to append the mandatory Work Item ID required by commit validation webhooks:
```bash
git commit --amend -m "[TASK-84920] Refactor pipeline artifact cache resolver"
git push origin main --force-with-lease
```

---

## 2. Container Image Build & Dual-Plane Registry Synchronization
### Q: Docker build or push fails with x509 certificate errors when accessing regional private registries.
**Root Cause**: Regional registry clusters frequently operate with enterprise internal root certificates that are not present in default public trust stores.
**Runbook Solution**:
When building or pushing images across dual-plane enterprise networks, append the `--insecure` parameter to bypass SSL validation on internal subnets:
```bash
# Push dual-plane container image bypassing certificate verification
aether-img push --insecure registry.internal.aetherpulse.net/tenant-prod/core-service:v2.4.1
```

---

## 3. JVM Container Workstation Tuning & Kubernetes Deployment
### Q: Maven build step succeeds, but the containerized microservice crashes on launch with OOMKilled (Exit Code 137).
**Runbook Solution**:
1. Confirm the Maven jar artifact location matches the designated tenant build target path: `/workspace/target/app-service-exec.jar`.
2. In the Kubernetes Pod deployment manifest, explicitly configure JVM memory limits to prevent the JVM heap from exceeding container cgroup memory limits:
```yaml
env:
  - name: JAVA_OPTS
    value: "-XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0 -XX:+ExitOnOutOfMemoryError"
  - name: TZ
    value: "Asia/Shanghai"
```

---

## 4. Mobile Coder Runner Troubleshooting
### Q: Mobile Coder execution agent crashes intermittently during compilation with JIT memory allocation errors.
**Root Cause**: Under certain high-concurrency Linux kernel configurations, Bun runtime JIT compilation conflicts with hardened security memory allocators.
**Runbook Solution**:
Temporarily disable JIT compilation in the shell environment prior to invoking the Mobile Coder process:
```bash
# Temporarily set environment variable to disable Bun JIT compilation
export BUN_JIT=0
# Launch Mobile Coder agent runner
./bin/mobile-coder-runner --port 8088 --config ./conf/runner.json
```
