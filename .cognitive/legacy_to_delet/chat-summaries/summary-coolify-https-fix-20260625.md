# Session: coolify-https-fix

## Since Last Summary
- Sesión anterior (self-contained-package): refactor cognitive_v2, absorcion ops_* en BCs, 12 feature tools creados
- Esta sesión: cambio de foco completo → diagnóstico y reparación de servidor Coolify en producción
- No se modificó código local; toda la actividad fue via SSH al servidor Oracle Cloud

## Goal
Diagnosticar el servidor Coolify con `diagnose-server.sh` y reparar el timeout HTTPS en dominios sslip.io

## Constraints & Preferences
- Operaciones exclusivamente por SSH (no acceso a UI de Coolify)
- Servidor: ubuntu@149.130.221.251 (Oracle Cloud)
- No romper el dominio principal game-reset-00001.duckdns.org (Coolify UI)
- Solución debe sobrevivir re-deploys automáticos

## Progress
### Done
- Ejecutado `scripts/diagnose-server.sh` (2 veces, pre y post fix)
- Reiniciado `coolify-proxy` para limpiar panic history (1 panic + 2 context canceled)
- Verificado certificados ACME: los 3 certs válidos (duckdns hasta 29-ago, sslip.io hasta 31-jul)
- Aclarado que errores ACME "index.php"/"test"/"test123" son bots/scanners, NO fallas de renovación
- Diagnosticado causa raíz del timeout HTTPS: contenedor web sin `traefik.http.services...loadbalancer.server.port` label
- Confirmado bug en Coolify 4.0.0-beta.462: no inyecta labels de service en docker-compose procesado
- Actualizado BD Coolify: `ports_exposes` 3000→8081, `fqdn` vacío→sslip.io, `custom_labels` regeneradas
- Forzado 3 deploys via `queue_application_deployment()` (tinker) — todos finished
- Creado override Traefik: `/data/coolify/proxy/dynamic/web-override.yaml`
- Optimizado override para usar alias de red estable `web` (sobrevive deploys sin scripts)
- Verificado que override sobrevive deploy automáticamente (sin hooks ni scripts manuales)
- Eliminado script `fix-traefik-web.sh` (innecesario con alias estable)
- Diagnóstico final: todos los endpoints funcionando

### In Progress
- (nada)

### Blocked
- (nada)

## Key Decisions
1. **Reiniciar coolify-proxy** — limpiar panic history y errores de Docker provider acumulados desde mayo
2. **No tocar ACME** — los errores "index.php" son ruido de scanners; certificados válidos y renovando correctamente
3. **Usar override de Traefik (dynamic config)** en vez de modificar labels del contenedor — más limpio y reversible
4. **Usar alias de red `web`** en vez del nombre del contenedor — el alias es estable entre deploys (viene del nombre del servicio en docker-compose), el nombre del contenedor cambia en cada deploy
5. **No usar post_deployment_command hook** — el hook de Coolify ejecuta dentro del contenedor, no en el host; el alias estable elimina la necesidad de cualquier hook/script
6. **Actualizar BD Coolify directamente** (ports_exposes, fqdn, custom_labels) — necesario porque el bug impide que Coolify genere las labels correctamente

## Next Steps
1. Reportar bug de Coolify beta.462 al equipo (labels `traefik.http.services...loadbalancer.server.port` no se aplican en deployments docker-compose)
2. Considerar actualizar Coolify a versión más estable cuando salga del beta
3. Limpiar imágenes Docker antiguas (1.4GB reclaimable): `docker image prune` en el servidor
4. El dominio `bwcowcwg...sslip.io` da 503 — no hay contenedor asignado (deployment anterior eliminado); eliminar certificado ACME si ya no se usa

## Critical Context
- **Branch**: `cognitive/gaps-analysis`
- **COMMIT ①**: `e210cbc` — feat: cognitive_v2 feature tools (12 files, 2160 insertions)
- **Servidor**: ubuntu@149.130.221.251 / SSH key: ~/develop/Active-projects/Oracle-Cloud/ssh-key-2026-01-31.key
- **Archivo clave en servidor**: `/data/coolify/proxy/dynamic/web-override.yaml` — override permanente de Traefik
- **Script de diagnóstico**: `scripts/diagnose-server.sh` (en repo local)
- **Bug Coolify**: beta.462 no genera `traefik.http.services.<name>.loadbalancer.server.port` ni `traefik.http.routers.<name>.service` en docker-compose procesado, aunque estén en `custom_labels` de la BD
- **BD Coolify**: apps table id=4, ports_exposes=8081, fqdn=https://uk80ggc4c00og4s0ocogckwo.149.130.221.251.sslip.io
- **Alias estable**: contenedor web tiene alias `web` en red `coolify` (DNSNames: web, <container-name>, <container-id>)
