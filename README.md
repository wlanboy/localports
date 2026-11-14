# localports

Wrapper um `oc port-forward` / `kubectl port-forward`: Namespaces auswählen, alle Services darin werden auf `localhost:<serviceport>` erreichbar.

## Nutzung

```bash
uv run localports                   # interaktiv
uv run localports -n app -n db -y   # ohne Rückfragen
uv tool install .                   # global als `localports` installieren
```

Optionen: `--cli oc|kubectl` (Standard: `oc`, sonst `kubectl`), `--address 0.0.0.0` (Standard `127.0.0.1`).

Auswahl in den Prompts: `1,3,5-7`, Namen, `*` für alle. Im Namespace-Prompt filtert `/text` die Liste.

## Port-Regeln

- Lokaler Port = Service-Port.
- Ist der Port belegt oder schon vergeben, wird der nächste freie genommen.
- Ports unter 1024 werden ohne root auf `port+8000` gelegt (80 → 8080, 443 → 8443).
- UDP-Ports, `ExternalName`-Services und Services ohne Selector werden übersprungen, weil `port-forward` sie nicht unterstützt.

Nach dem Start listet das Tool die Adressen auf, HTTP- und HTTPS-Ports als klickbare URL (Strg+Klick im Terminal). Das Protokoll wird aus `appProtocol`, dem Port-Namen (`http-…`, `https-…`, `grpc-…`) und bekannten Ports wie 443 abgeleitet. Nicht-HTTP-Ports wie DNS oder gRPC erscheinen als `localhost:port`.

Bricht ein Forward ab (z. B. bei einem Pod-Neustart), wird er automatisch neu gestartet. Strg+C beendet alle.

## Timeouts

localports übergibt keine Timeout-Flags, es gelten die Standardwerte von `kubectl` bzw. `oc` (das `oc`-Kommando nutzt denselben Code):

| Timeout | Standard | Wirkung |
|---|---|---|
| `--pod-running-timeout` | `1m` | So lange wartet `port-forward`, bis ein Pod hinter dem Service läuft. Danach bricht es ab. |
| `--request-timeout` | `0` (keins) | Gilt für einzelne API-Requests. Die laufende Port-Forward-Verbindung begrenzt es nicht. |
| Kubelet `streamingConnectionIdleTimeout` | `4h` | Fließen so lange keine Daten, schließt der Node die Verbindung. Der Wert wird im Cluster konfiguriert, nicht am Client. |

Load Balancer oder Proxys vor dem API-Server haben oft eigene, kürzere Idle-Timeouts und trennen ungenutzte Forwards dann früher.

Endet `port-forward` aus einem dieser Gründe, startet localports den Forward neu: zuerst nach 1 s, bei wiederholtem Abbruch mit doppelter Wartezeit bis maximal 30 s. Lief ein Forward länger als 30 s stabil, beginnt die Wartezeit wieder bei 1 s.
