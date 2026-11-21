# NetSentinel Node (Motorola Moto E7 Plus)

Daemon POSIX instalado em `/data/adb/netsentinel/` com inicialização automática no boot via `/data/adb/service.d/99-netsentinel.sh` (compatível com Magisk v31.1 e KernelSU-Next).

## Comandos Úteis via ADB (Root)

```bash
# Checar telemetria de bateria e tomada
adb -s ZF52366RHQ shell su -c '/data/adb/netsentinel/battery_limiter.sh --status'

# Executar um único ciclo completo (smoke test)
adb -s ZF52366RHQ shell su -c '/data/adb/netsentinel/netsentinel_node.sh --once'

# Aprender whitelist inicial de roteadores vizinhos no apartamento
adb -s ZF52366RHQ shell su -c '/data/adb/netsentinel/spectrum_auditor.sh --learn-whitelist'

# Consultar últimos eventos da caixa-preta
adb -s ZF52366RHQ shell su -c 'tail -n 20 /data/adb/netsentinel/blackbox.log'

# Consultar logs do daemon
adb -s ZF52366RHQ shell su -c 'tail -n 40 /data/adb/netsentinel/logs/node.log'
```
