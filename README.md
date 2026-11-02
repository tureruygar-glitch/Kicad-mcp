KiCad 10'u Claude ile canlı kontrol eden MCP sunucusu. 102 araç.

## Yenilikler
- **Pad bazlı yerleşim:** `place_near_pad`, `place_on_edge`, `place_relative`, `arrange_row`, `flip_footprint`, `check_placement`
- **Pad bazlı routing:** `route_pads` iki pad'i adıyla bağlar; genişlik netclass'tan gelir, ince pinlerde yol otomatik incelir. Her sonuçta kısa devre ve clearance uyarıları döner.
- **Freerouting ile otomatik routing:** `autoroute` mevcut yolları kilitler, istenen netclass'ları atlar (ör. elle çizilen güç yolları), sonunda zone'ları doldurup DRC çalıştırır.
- **Güç bütçesi:** `analyze_power_budget` şemadan her besleme netinin akımını hesaplar; regülatör ve sürücü sınırlarını aşınca uyarır, netclass önerir.
- **Akıma göre yol genişliği:** `calc_track_width` (IPC-2221) ve `configure_netclasses`
- **Parça veritabanı:** 16 yaygın parça; 13'ü üretici datasheet'lerinden doğrulandı (kaynak ve sayfa numarasıyla).
- **Kartı görme:** `snapshot_board` kartın PNG görüntüsünü verir.

## Kurulum
Gereksinimler: KiCad 10 (API sunucusu açık), [uv](https://docs.astral.sh/uv/). `autoroute` için Java 21+ (Java 25 önerilir).

    claude mcp add kicad --scope user -- uvx --from https://github.com/tureruygar-glitch/Kicad-mcp/archive/refs/tags/v1.1.0.zip kicad10-mcp

Ayrıntılar README'de.

## Test
KiCad 10.0.5 üzerinde Arduino Nano + TB6612FNG kartı baştan sona çizildi: 0 DRC hatası, 0 çizilmemiş bağlantı. Temiz bir `uvx` kurulumu canlı KiCad'e karşı 13/13 kontrolü geçti.

## Bilinen sınırlamalar
- Bakır katmandaki yazılar Freerouting'e engel olarak gitmez; `autoroute` sonrası DRC raporunu okuyun.
- KiCad 10.0.5'te şema editöründe ERC çalıştırmak KiCad'i çökertebiliyor (KiCad hatası).
- `configure_netclasses` proje KiCad'de kapalıyken çalışır.

## Eski sürümden farklar
Eski `kicad10-mcp.exe` artık sunulmuyor. Kurulum `uvx` ile yapılıyor.
