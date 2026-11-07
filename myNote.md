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

## 15. Benchmarki: wnioski z listopada 2026

Przebieg: 1 raz na model, `--now 2026-06-10T09:00:00+02:00`, te same 16 pytań.
Surowe odpowiedzi i wywołania narzędzi: `eval/benchmarks/*.json`.

1. **Gemini 3.5 Flash Lite (Baseline API):**
   - Benchmark: **15/16**. Pełna ewaluacja (`eval/results.md`): **16/16**.
   - Jedyny błąd w benchmarku: `refund-1010` — model wywołał
     `calculate_refund` z `delay_days: 1` zamiast `0`, więc policzył zwrot na
     moment startu (0%) i podał 150.00 EUR zamiast 170.00 EUR. To nie
     halucynacja, tylko błędna interpretacja „exactly 24 hours before start”.
   - Free tier ma limit ~15 zapytań/min.

2. **Gemma 4 12B QAT (lokalny, LM Studio):**
   - Wynik: **11/16**, tool accuracy 100%, średnio 32.47 s, 6.2 tok/s.
   - Wszystkie 5 niepowodzeń to pytania refundowe: kwota była poprawna, ale
     odpowiedź nie zawierała jawnego „%” wymaganego przez rubric.
   - Wolna: ~32 s na pytanie.

3. **Gemma 4 26B A4B QAT (lokalny, LM Studio, MoE):**
   - Wynik: **10/16**, tool accuracy 100%, średnio 9.34 s, 38.0 tok/s.
   - Failuje te same 5 refundów (brak „%”) plus `age-14-ebike`: odpowiedź
     „from 16 years old” jest merytorycznie poprawna, ale regex wymaga słowa
     `no`/`not`/`minimum`/`at least`.
   - Jest ~3,5× szybsza od 12B; 12B wygrywa tylko mniejszym VRAM-em.

### Wnioski

- **Tool calling:** 100% we wszystkich modelach. Lokalne modele wywołały
  `calculate_refund` i użyły jego wyniku; nie zgadywały kwot.
- **Rubric:** wymóg jawnego „%” zaniża wyniki lokalnych modeli. To nie błąd
  modeli, ale trzeba to opisać, bo inaczej 11/16 i 10/16 wyglądają jak brak
  umiejętności liczenia.
- **Latencja:** 26B-A4B jest szybsza od 12B mimo większej liczby parametrów
  (MoE, ~4B aktywnych). 12B ma przewagę tylko w VRAM.
- **Wariancja:** to pojedyncze przebiegi; Gemini raz miał 16/16, raz 15/16.
  Nie traktować tabeli jako rankingu.
- **Odtwarzalność:** lokalne modele nie są już dostępne; w repo zostają
  surowe JSON-y jako zapis przebiegu.


