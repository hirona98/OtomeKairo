# OtomeKairo

会話 1 サイクルの最小縦切りとして、HTTPS の最小 API サーバを実装している。

## 実行

```bash
OTOMEKAIRO_TLS_CERT_FILE=/path/to/cert.pem \
OTOMEKAIRO_TLS_KEY_FILE=/path/to/key.pem \
PYTHONPATH=src \
python3 -m otomekairo.run
```

データはデフォルトで `var/otomekairo/` に保存される。
