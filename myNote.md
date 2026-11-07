# Notatki robocze

Osobisty brudnopis do zadania CityWheels: otwarte pytania, decyzje do
zweryfikowania i rzeczy do sprawdzenia przed ostatnim commitem.

Ten plik nie jest częścią rozwiązania. Przed wysłaniem linku do repozytorium
przenieś z niego to, co ważne, do `SOLUTION.md` (albo usuń cały plik).

---

## 1. Polityka zaokrąglania — zdecydowana, ale warta powrotu

Obecny wybór: `ROUND_HALF_UP`, stosowany **raz**, do końcowego wyniku
(`money.quantize`). Wszystkie kwoty to `Decimal`; `bookings.json` jest
czytany z `parse_float=Decimal`, więc `float` nigdy nie dotyka obliczeń.

Otwarte kwestie:

- **Kto zyskuje przy trafieniu w połówkę?** Przy **zwrocie** (pieniądze
  wychodzą ze sklepu) half-up daje klientowi maksymalnie pół grosza. Przy
  **obciążeniu** (opłata za przetrzymanie, szkoda) ta sama reguła daje
  maksymalnie pół grosza sklepowi. Jeden globalny `ROUNDING` jest więc
  bezpieczny tylko dopóki liczymy zwroty; przyszła opłata musi wybrać własną
  politykę jawnie.
- **Alternatywy:** `ROUND_HALF_EVEN` (bezstronny przy wielu transakcjach,
  standard w księgowości) i obcinanie (zawsze zaniża zwrot klientowi —
  odrzucone).
- **Czy to ma dziś znaczenie?** Nie. Ceny są w całych euro, a procenty zwrotu
  to 0/50/100, więc zaokrąglanie nigdy nie zmienia wartości. Ryzyko pojawia
  się dopiero, gdy ceny albo procenty dostaną grosze.
- **Zabezpieczenia, które już są:** dokładna arytmetyka `Decimal`,
  pojedyncze zaokrąglenie na końcu, jawna stała `ROUNDING`, testy trafienia
  w połówkę (`50% z 12.35 -> 6.18`, `50% z 12.34 -> 6.17`), test braku
  floatowego śmiecia.
- **Zabezpieczenia dodane w etapie 2:** niezmienniki — części sumują się do
  całości, `total <= rental_price + deposit`, zwrot booking fee zawsze zero,
  rezerwacje nieanulowalne nic nie zwracają.
- **Do opisania w `SOLUTION.md`:** krótka sekcja „Rounding policy” z regułą,
  informacją kogo faworyzuje i kiedy zaczęłaby mieć znaczenie.
- **Pytanie do rekrutera:** czy zwroty mają być zaokrąglane w górę
  (`ROUND_CEILING`) zamiast half-up, żeby były jednoznacznie korzystne dla
  klienta? To zmiana jednej linijki, jeśli odpowiedź brzmi „tak”.

## 2. Semantyka zwrotu — nasze odczytanie dokumentów

- „Ile dostanę z powrotem” = zwrot najmu + kaucja. Booking fee nigdy nie
  wraca.
- Kaucja zawsze wraca w pełni dla rezerwacji `confirmed`, także w przypadku
  `< 24 h`, gdzie zwrot najmu wynosi 0%.
- Statusy `in_progress`, `completed`, `cancelled`: nic nie wraca — łącznie
  z kaucją. To nasze odczytanie zdania „nothing is refunded for them”.
  **Zweryfikować**, jeśli rekruter interpretuje to inaczej.
- Rezerwacje z voucher: zwrot jako nowy voucher, nigdy jako pieniądze;
  kaucja wraca tą samą metodą, jaką była zapłacona.
- Granice są inkluzywne: `>= 48 h` -> 100%, `>= 24 h i < 48 h` -> 50%,
  `< 24 h` -> 0%. Porównujemy dokładny `timedelta`, nigdy „pełne godziny”.
- Rezerwacja `confirmed`, której start już minął, nadal podlega regule
  `< 24 h` (0% + kaucja). Nie ma takiej w danych, ale jest pokryta testem.

## 3. Osobliwości danych

- `rental_price_eur` jest zapisane w danych — używamy go do zwrotu, nie
  przeliczamy od nowa. Opcjonalny test spójności z `prices.md`: CW-1009 to
  7 dni z rabatem 15% (140 -> 119).
- CW-1010 i CW-1006 mają `booking_fee_eur = 0` (rezerwacja w sklepie).
- Tylko CW-1004 była zapłacona voucherem.
- Wszystkie dostarczone rezerwacje są z czerwca. Zachowanie przy zmianie czasu
  pokrywa syntetyczny test październikowy, nie prawdziwe dane.

## 3a. Pułapki znalezione podczas implementacji

- Python odejmuje dwie daty ze strefą, jeśli mają **ten sam obiekt
  `ZoneInfo`**, tak jakby były naiwne (arytmetyka zegara ściennego),
  ignorując zmianę offsetu przy DST. `clock.delta_until` konwertuje obie
  strony do UTC; pokryte testem na 23/25 października 2026 (48 godzin
  bezwzględnych, 47 godzin na zegarze).
- `json.loads(..., parse_float=Decimal)` to ten szczegół, który trzyma kwoty
  z dala od floata. Bez niego każdy późniejszy krok na `Decimal` jest tylko
  kosmetyką.
- Niezmienniki zwrotu są testowane dla każdej dostarczonej rezerwacji:
  części sumują się do całości, `total <= rental_price + deposit`, zwrot
  booking fee jest zerowy, rezerwacje nieanulowalne nic nie zwracają.

## 4. Wyszukiwanie polityk

- Deterministyczne scoringowanie keywordów po nagłówkach Markdown; bez
  embeddingów i bez sieci.
- Ryzyko: parafrazy („how late are you open”, „cost of an electric bike”)
  mogą nie trafić w właściwy fragment. Testować na wszystkich przykładowych
  pytaniach z README.
- Ograniczyć rozmiar fragmentów zwracanych modelowi; `top_k = 3` powinno
  wystarczyć.
- Zwracać `source` i `heading`, żeby agent mógł wskazać, skąd wzięła się
  odpowiedź.
- **Pytania testowe bez słów kluczowych:** jeśli pytanie nie dzieli ani
  jednego słowa z dokumentami (np. „How expensive is renting a two-wheeler?”),
  `search_policies` zwróci `found: false` — i to jest poprawne zachowanie
  wyszukiwarki, ale model może nie wiedzieć, co z tym zrobić. W ewaluacji
  trzeba mieć co najmniej jedno takie pytanie i sprawdzić, czy agent mówi
  „nie mam tego w dokumentach, skontaktuj się ze staffem”, zamiast zmyślać.
  Dopiero jeśli to zawiedzie, rozważyć synonimy albo embeddingi.

## 5. Zachowanie modelu do sprawdzenia

- Model musi wywołać `calculate_refund`, a nie liczyć w głowie. W ewaluacji
  sprawdzać wywołane narzędzia; rozważyć wymuszenie wyboru narzędzia dla
  pytań o zwrot, jeśli Gemini będzie zawodny.
- Język odpowiedzi: decyzja projektowa to **tylko angielski**. Sprawdzić, czy
  prompt nie zaczyna lustrzanie odpowiadać po polsku na polskie pytanie.
- Pytania poza zakresem (stolica Australii, hasło do Netflixa) muszą zostać
  odrzucone i wskazać `help@citywheels.example` / `+48 000 000 000`.
- Zła pogoda, szkoda i kradzież to decyzje staffu — agent ma przekazać
  sprawę, a nie decydować.
- Nieznany numer rezerwacji: powiedzieć „not found”, nigdy nie wymyślać
  szczegółów.

## 6. Operacje

- Sprawdzić, czy `gemini-2.5-flash` jest dostępny z prawdziwym kluczem API;
  mieć w odwodzie model zapasowy.
- Darmowy tier ma limity: retry z backoffem i mały zestaw pytań w ewaluacji.
- CI uruchamia tylko testy z fake modelem (bez klucza). Ewaluacja jest
  uruchamiana ręcznie.
- Windows `core.autocrlf=true` produkuje ostrzeżenia LF/CRLF; rozważyć
  `.gitattributes` z `* text=auto eol=lf`.
- Zdecydować, czy `myNote.md` zostaje w repozytorium przed wysłaniem.

## 7. Projekt ewaluacji

- Co najmniej 10 pytań, co najmniej 2 zwroty, co najmniej 2 odmowy, plus
  nieznany numer rezerwacji.
- Asercje: oczekiwany podzbiór narzędzi, regex na kwoty (akceptować
  `340.00`), odmowa wykrywana po danych kontaktowych, brak wymyślonych liczb.
- Jedno pytanie celowo bez wspólnych słów z dokumentami — sprawdza fallback
  „nie wiem, zapytaj staff”, a nie trafność wyszukiwarki.
- Uczciwie raportować porażki w `eval/results.md`, z jednym zdaniem
  uzasadnienia dla każdej.
- Pytania deterministyczne; `temperature = 0`.

## 8. Warstwa modelu

- Gemini 2.5 wymaga odsyłania `thought_signature` razem z function call.
  Dlatego `LLMResponse.raw` trzyma oryginalny `Content` z SDK, a agent odsyła
  go bez przebudowy. Bez tego prawdziwe wywołanie może dostać 400.
- Retry tylko dla 429/5xx i błędów sieci; 400 nie jest ponawiane.
- `FakeLLM` siedzi w `llm.py`, nie w testach, żeby ewaluacja też mogła go użyć.
- Prompt zabrania liczenia w głowie i mówi wprost, że agent nie anuluje
  rezerwacji — tylko informuje.
- Przed ewaluacją sprawdzić z prawdziwym kluczem, czy model nie pomija
  `get_booking` przed `calculate_refund` i czy nie próbuje liczyć sam.

## 9. CLI

- Kody wyjścia: `0` sukces, `1` błąd działania (model/narzędzia/limit tur),
  `2` błąd użycia lub konfiguracji (brak klucza, złe dane, puste pytanie).
- Prompt REPL idzie na `stderr`, żeby `stdout` został czysty dla odpowiedzi.
- `--json` w REPL wypisuje jeden obiekt JSON na linię; dla pojedynczego
  pytania ładnie sformatowany JSON.
- Bez `--verbose` logi `citywheels` są wyciszone; użytkownik widzi tylko
  komunikat z `AgentResult`, bez tracebacku.
- Testy CLI wstrzykują `client_factory`, więc nie potrzebują klucza ani sieci.
- Prawdziwy smoke test z Gemini do zrobienia po ustawieniu `.env`.

## 10. Model i konsola (ustalone przy smoke teście)

- `gemini-2.5-flash` zwraca `404` dla nowych użytkowników (listopad 2026);
  `gemini-3.8-flash` i `gemini-3.7-flash` były przeciążone (`503`/`504`).
  Działa `gemini-3.6-flash` — ustawiony jako domyślny.
- `response.text` wypisuje ostrzeżenie, gdy odpowiedź zawiera function call.
  Dlatego `parse_response` czyta tekst bezpośrednio z `candidates[0].content.parts`.
- Konsola Windows nie jest UTF-8: CLI zamienia `€` na `EUR`, a myślniki na `-`.
  `--json` używa `ensure_ascii`, więc dane po sparsowaniu są dokładne.
- Testy nie mogą czytać lokalnego `.env`; autouse fixture wyłącza `load_dotenv`.
- Smoke test (3.6): godziny otwarcia, zwrot CW-1002 = 340.00 EUR, odmowa
  pytań spoza zakresu i nieznany booking CW-9999 działają.

## 11. Ewaluacja (etap 7)

- `eval/questions.json` ma 16 pytań: 7 o polityki, 6 o zwroty, 2 odmowy,
  1 nieznany booking. Każde pytanie ma oczekiwane narzędzia i regexy.
- Harness sprawdza lokalnie: narzędzia, wzorce wymagane/zabronione, dane
  kontaktowe przy odmowie. Wszystko deterministyczne, `temperature = 0`.
- `eval/run_eval.py` obsługuje `--limit`, `--only`, `--model`, `--now`,
  `--delay`, `--output`. Kod wyjścia `3` = część pytań zablokowana limitem.
- **Limit darmowego tieru:** `gemini-3.6-flash` ma 20 żądań/dzień, a pełny
  zestaw zużywa ~30 (każde pytanie to 2 tury). Dlatego domyślnym modelem
  został `gemini-3.5-flash-lite` (15 żądań/min, wyższy limit dzienny).
  `gemini-3.6-flash` i `gemini-3.5-flash` działały dobrze pojedynczo, ale
  wyczerpały dzienny limit.
- Gdy Gemini podaje `retryDelay` większy niż 300 s, klient rzuca
  `QuotaExhausted` zamiast ponawiać 5 razy. Ewaluacja oznacza wtedy resztę
  pytań jako `BLOCKED` — nie udaje, że to błędy modelu.
- Gdy `retryDelay` jest mały (limit na minutę), klient czeka dokładnie tyle,
  ile mówi serwer, a nie tylko własny backoff.
- Poprawka promptu: pytania o usługi CityWheels są w zakresie, nawet gdy
  odpowiedź brzmi „nie” (np. „czy sprzedajecie rowery?”). Wcześniej lite
  model odmawiał bez szukania w politykach.
- **Wynik: 16/16 na `gemini-3.5-flash-lite`** (`eval/results.md`).
  Odpowiedzi zawierają poprawne kwoty, rozbicie zwrotu i metodę płatności.

## 12. CI (etap 8)

- `.github/workflows/tests.yml`: Ubuntu, Python 3.11 i 3.12, cache pip,
  `pip install -e ".[dev]"`, `pytest -q`. Bez klucza API i bez ewaluacji.
- Workflow nie odwołuje się do żadnego sekretu; uprawnienia tylko
  `contents: read`.
- Test w czystym venv na Windows ujawnił brak `tzdata`: globalny Python miał
  ją przypadkiem, ale świeża instalacja nie. Dodane warunkowo
  `tzdata>=2024.1; platform_system == "Windows"` do zależności.
- Po tej poprawce czysty venv przechodzi 200 testów, bez
  `RequestsDependencyWarning` (potwierdza, że to artefakt lokalnego środowiska).

## 13. Dokumentacja (etap 9)

- `README.md` opisuje projekt, quickstart, użycie, testy, ewaluację, układ
  katalogów i konfigurację. Przykładowy transcript pochodzi z prawdziwych
  odpowiedzi modelu.
- `SOLUTION.md` zawiera architekturę, wybór modelu, decyzje projektowe, wyniki
  ewaluacji, konkretne błędy asystenta AI i sposoby ich wykrycia, znane
  ograniczenia oraz `RequestsDependencyWarning`.
- Link do sekcji ostrzeżenia w `README.md` używa kotwicy
  `#9-known-local-environment-warning`.
- Świadomie nie dodaję filtra `warnings.filterwarnings`: ukrywałby też
  prawdziwe ostrzeżenia.
- Wyniki w dokumentacji są zgodne z `eval/results.md`: 16/16 na
  `gemini-3.5-flash-lite`.

## 14. Etap 10 (opcjonalny)

- **`delay_days` w `calculate_refund`:** 0 = teraz, 1 = jutro, zakres 0-30.
  Kod przesuwa `now` i nadal stosuje reguły 48/24 h; model wybiera tylko liczbę
  dni. Wynik zawiera `delay_days` i `cancel_at`. Błędne wartości dają
  `invalid_arguments`, nie wyjątek.
- **Pamięć sesji:** `run_agent(..., history=...)` przyjmuje tylko tekstowe tury
  `user`/`assistant`. Stare wywołania narzędzi nie są odtwarzane. REPL z
  `--session` dopisuje udane tury; bez flagi każde pytanie jest bezstanowe.
  Limit: 10 tur i ~6000 znaków; nieudana tura nie trafia do historii.
- **Provider:** `LLM_PROVIDER=gemini|openai-compatible`, `LLM_MODEL`,
  `LLM_API_KEY`, `LLM_BASE_URL`. Jeden `create_client()` w `llm.py` wybiera
  adapter. Gemini jest domyślny i nadal działa; stare `GEMINI_MODEL` /
  `GEMINI_API_KEY` są aliasami.
- **Adapter OpenAI:** zamienia schematy na małe litery, generuje `tool_call_id`,
  paruje wyniki `role: tool`, czyta `usage`, obsługuje `retry-after` i
  `QuotaExhausted`. Przetestowany lokalnym serwerem HTTP (nie tylko mockiem).
- **Benchmark:** `eval/benchmark.py` + `eval/benchmark_models.json` (2 API +
  2 LM Studio), 3 przebiegi, te same pytania i `--now`. Zapisuje
  `eval/benchmarks/summary.md`, raporty `.md` i surowe `.json`. VRAM i cold
  start trzeba dopisać ręcznie.
- **Smoke test Gemini:** sesja `--session` z CW-1002 dała 340.00 EUR teraz i
  300.00 EUR jutro; model poprawnie użył `delay_days=1`.
- **Testy:** 277 przechodzą offline. Nowe pliki: `test_openai_adapter.py`,
  `test_benchmarks.py`; rozszerzone testy zwrotów, toolboxa, agenta, CLI i
  konfiguracji.
- **Uwaga:** `hours-saturday` czasem gubi godzinę zamknięcia — to znana
  flakiness modelu, nie błąd kodu; przy powtórce przechodzi.

## 15. Modele do benchmarku (research 2026-11-07)

Cel: dwa modele lokalne (16 GB VRAM i 8-10 GB VRAM) oraz jeden darmowy model
API porównywalny z `gemini-3.5-flash-lite`. Wszystkie muszą wspierać tool
calling, bo agent bez tego nie działa.

### Lokalny #1 — do 16 GB VRAM

**`openai/gpt-oss-20b`** (LM Studio)

- OpenAI open-weight, Apache 2.0; 21B parametrów, 3.6B aktywnych (MoE).
- Natywna kwantyzacja MXFP4; LM Studio podaje minimum 12 GB pamięci, a OpenAI
  opisuje model jako mieszczący się w 16 GB VRAM. Realnie ~12-13 GB + kontekst.
- Tool use i reasoning są wbudowane; kontekst do 131k.
- Zaleta: ten sam model można uruchomić lokalnie i przez darmowe API (Groq),
  co daje ciekawe porównanie „te same wagi, inny serving”.
- Link: https://lmstudio.ai/models/openai/gpt-oss-20b
- W LM Studio ustaw Reasoning Effort na `low` albo `medium`; `high` tylko
  wydłuża odpowiedzi i benchmark.

### Lokalny #2 — 8-10 GB VRAM

**`google/gemma-4-12b`** (LM Studio)

- 12B, encoder-free multimodal, trenowany do tool use; kontekst do 256k.
- LM Studio podaje minimum 7 GB pamięci; w Q4 realnie ~7-8 GB, więc mieści się
  na 8 GB VRAM (na 10 GB jest komfortowo).
- Ma przełącznik „Enable Thinking” (domyślnie włączony). Do benchmarku lepiej
  go wyłączyć — myślenie zwiększa latencję i utrudnia porównanie.
- Link: https://lmstudio.ai/models/google/gemma-4-12b
- Alternatywa, jeśli chcesz rodzinę Qwen: `qwen3:14b-q4_K_M` (~10-11 GB,
  sprawdzony tool calling, ale tighter na 8 GB).

### Darmowy model API — odpowiednik Gemini Flash Lite

**`openai/gpt-oss-20b` na Groq (free plan)**

- To ten sam model co lokalny #1, więc wydajnościowo jest najbliżej
  `gemini-3.5-flash-lite` z całej trójki.
- Endpoint OpenAI-compatible: `https://api.groq.com/openai/v1`.
- Free plan: 30 RPM, 1 000 RPD, 8 000 TPM, 200 000 TPD.
- Tool calling: tak (Groq dokumentuje local tool calling na tym modelu).
- Linki: https://console.groq.com/docs/models ,
  https://console.groq.com/docs/rate-limits ,
  https://console.groq.com/docs/openai
- Mocniejsza opcja za darmo: **`openai/gpt-oss-120b`** (te same limity,
  ~500 tok/s, jakościowo wyżej niż Flash Lite).
- Inna opcja: **`qwen/qwen3.8-27b`** (30 RPM, 1 000 RPD, 8k TPM; 131k kontekstu).
- OpenRouter free ma tylko ~50 requestów/dzień, więc nie nadaje się na pełny
  benchmark 16 pytań × 3 przebiegi; co najwyżej na smoke test.

### Wpisy do `eval/benchmark_models.local.json`

```json
{
  "label": "lmstudio-gpt-oss-20b",
  "provider": "openai-compatible",
  "model": "openai/gpt-oss-20b",
  "base_url": "http://localhost:1234/v1",
  "api_key": "lm-studio",
  "note": "OpenAI open-weight; MXFP4; ~12-13 GB VRAM; tool use."
}
```

```json
{
  "label": "lmstudio-gemma-4-12b",
  "provider": "openai-compatible",
  "model": "google/gemma-4-12b",
  "base_url": "http://localhost:1234/v1",
  "api_key": "lm-studio",
  "note": "Gemma 4 12B; ~7-8 GB VRAM; tool use; thinking off."
}
```

```json
{
  "label": "groq-gpt-oss-20b",
  "provider": "openai-compatible",
  "model": "openai/gpt-oss-20b",
  "base_url": "https://api.groq.com/openai/v1",
  "api_key_env": "GROQ_API_KEY",
  "note": "Free: 30 RPM / 1K RPD / 8K TPM / 200K TPD; tool use."
}
```

Klucz Groq trzymaj w `.env` (plik jest ignorowany przez Git):

```dotenv
GROQ_API_KEY=...
```

### O czym pamiętać przy benchmarku

- **Sprawdź dokładne id modelu:** `curl http://localhost:1234/v1/models`.
  LM Studio może wystawić inną nazwę niż na stronie modelu.
- **Groq 8k TPM to wąskie gardło:** jedno pytanie z pętlą narzędzi potrafi
  zużyć 2-4k tokenów. Zacznij od `--runs 1 --delay 8`, dopiero potem 3 przebiegi.
- **Nie porównuj 1 przebiegu z 3.** Jeśli limit zmusi do 1 przebiegu, zaznacz
  to w raporcie.
- **Wyłącz thinking** (Gemma 4) albo ustaw niski reasoning effort
  (gpt-oss), inaczej latencja będzie zawyżona.
- **OpenRouter free (~50 RPD)** nie wystarczy na pełny benchmark; użyj Groq.
- **Nie wpisuj kluczy API do `benchmark_models.json`** — używaj
  `api_key_env` i `.env`.
- **Zweryfikuj tool calling przed pełnym runsem:** jedno pytanie
  `--only hours-saturday,refund-1002 --runs 1`.
- **VRAM i cold start zapisz ręcznie** — runner ich nie mierzy.

