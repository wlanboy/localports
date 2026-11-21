# localports

Wrapper um `oc port-forward` / `kubectl port-forward`: Namespaces auswählen, alle Services darin werden auf `localhost:<serviceport>` erreichbar.

## Nutzung

```bash
uv run localports                   # interaktiv
uv run localports -n app -n db -y   # ohne Rückfragen
uv tool install .                   # global als `localports` installieren
```

Optionen: `--cli oc|kubectl` (Standard: `oc`, sonst `kubectl`), `--address 0.0.0.0` (Standard `127.0.0.1`).

`--address` nimmt wie `port-forward` eine kommagetrennte Liste aus IPs und `localhost`. Das Tool prüft die Adresse vor dem ersten Prompt und bricht mit einer Fehlermeldung ab, wenn sie keine IP ist oder auf diesem Rechner nicht existiert. Bei `localhost` reicht es, wenn sich eine der beiden Adressen 127.0.0.1 und ::1 binden lässt, etwa wenn IPv6 abgeschaltet ist.

Mit `--cli oc` greift das Tool auf `oc projects` zurück, wenn der User keine Namespaces listen darf. Mit `--cli kubectl` gibt es diesen Rückgriff nicht.

Auswahl in den Prompts: `1,3,5-7`, Namen, `*` für alle. Im Namespace-Prompt filtert `/text` die Liste.

## Port-Regeln

- Lokaler Port = Service-Port.
- Ist der Port auf der `--address` belegt oder schon vergeben, wird der nächste freie genommen.
- Die lokalen Ports werden erst nach der Service-Auswahl vergeben. Nicht gewählte Services belegen keine Ports.
- Ports unter 1024 werden auf `port+8000` gelegt (80 → 8080, 443 → 8443), wenn sie sich nicht binden lassen. Ist `ip_unprivileged_port_start` gesenkt (z. B. für Podman), bleibt es beim Original-Port.
- UDP-Ports, `ExternalName`-Services und Services ohne Selector werden übersprungen, weil `port-forward` sie nicht unterstützt.

Nach dem Start listet das Tool die Adressen auf, HTTP- und HTTPS-Ports als klickbare URL (Strg+Klick im Terminal). Das Protokoll wird aus `appProtocol` (auch `kubernetes.io/ws` und `kubernetes.io/wss`), dem Port-Namen (`http-…`, `https-…`, `ws-…`, `wss-…`, `grpc-…`) und bekannten Ports wie 443 abgeleitet. Nicht-HTTP-Ports wie DNS oder gRPC erscheinen als `localhost:port`.

Bricht ein Forward ab (z. B. bei einem Pod-Neustart), wird er automatisch neu gestartet. Strg+C beendet alle, ebenso das Schließen des Terminals oder ein `kill`.

Ausnahme: Hat ein anderer Prozess einen lokalen Port zwischen Prüfung und Start belegt, meldet `port-forward` „Unable to listen on port …“. Ein Neustart würde jedes Mal am selben Port scheitern. localports beendet dann alle Forwards und bricht mit einer Fehlermeldung ab (Exit-Code 1). Beim nächsten Start vergibt localports den Port neu.

## Timeouts

Die kurzen Abfragen vor dem Start (Kontext, Namespaces, Services) laufen mit `--request-timeout=10s` und werden nach 20 s hart abgebrochen. So hängt das Tool nicht, wenn der Cluster nicht erreichbar ist oder ein Login-Plugin wartet.

Für `port-forward` selbst übergibt localports keine Timeout-Flags, es gelten die Standardwerte von `kubectl` bzw. `oc` (das `oc`-Kommando nutzt denselben Code):

| Timeout | Standard | Wirkung |
|---|---|---|
| `--pod-running-timeout` | `1m` | So lange wartet `port-forward`, bis ein Pod hinter dem Service läuft. Danach bricht es ab. |
| `--request-timeout` | `0` (keins) | Gilt für einzelne API-Requests. Die laufende Port-Forward-Verbindung begrenzt es nicht. |
| Kubelet `streamingConnectionIdleTimeout` | `4h` | Fließen so lange keine Daten, schließt der Node die Verbindung. Der Wert wird im Cluster konfiguriert, nicht am Client. |

Load Balancer oder Proxys vor dem API-Server haben oft eigene, kürzere Idle-Timeouts und trennen ungenutzte Forwards dann früher.

Endet `port-forward` aus einem dieser Gründe, startet localports den Forward neu: zuerst nach 1 s, bei wiederholtem Abbruch mit doppelter Wartezeit bis maximal 30 s. Lief ein Forward länger als 30 s stabil, beginnt die Wartezeit wieder bei 1 s.

## Docker

Das Image enthält `kubectl` aus `registry.k8s.io/kubectl`, aber kein `oc`. Die kubeconfig gehört nach `/kube/config`:

```bash
docker build --build-arg REVISION=$(git rev-parse HEAD) -t localports .
docker run --rm -it --init --network host --read-only --tmpfs /tmp \
  -v ~/.kube/config:/kube/config:ro localports -n app
```

- `--network host` ist nötig, weil die lokalen Ports erst zur Laufzeit feststehen und sich nicht vorab mit `-p` freigeben lassen. Das funktioniert nur unter Linux.
- `-it` braucht das Tool für die Prompts. Ohne Terminal läuft es nur mit `-n` und `-y`.
- `--init` sorgt dafür, dass `docker stop` den Prozess sofort beendet, auch während eines Prompts.
- Der Container läuft als UID 1000. Die kubeconfig muss für diese UID lesbar sein.
- Login-Plugins (`exec` in der kubeconfig, etwa OIDC oder Cloud-CLIs) fehlen im Image. Mit solchen Kontexten klappt die Anmeldung nur über ein Token in der kubeconfig.
- Ports unter 1024 landen auf `port+8000`, weil der Prozess nicht als root läuft. Mit `--network host` gilt `ip_unprivileged_port_start` des Hosts.

Die kubectl-Version steht im Dockerfile als `KUBECTL_VERSION` und lässt sich mit `--build-arg KUBECTL_VERSION=v1.xx.y` überschreiben.

## Entwicklung

```bash
uv run pytest        # Tests
uv run ruff check    # Linter
uv run pyright       # Typprüfung
```
