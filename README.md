# stockAutomation
Tinkering with Google Finance data

## Decision tree combinator (new)

You write the rules. The engine expands every AND combination and draws a yes/no decision tree. Research only — it does not paper, live-unlock, or ENABLE anything.

```bash
cd goldpetal
python3 test_decision_tree.py
python3 decision_tree.py --demo
python3 decision_tree_app.py --port 8791
# open http://127.0.0.1:8791/
```

- Add your own rules (name + when + long/short/filter/no-trade), or click catalog chips (green candle, volume up, above VWAP, …).
- Rules in the same **group** cannot both be true (green vs red, above vs below VWAP).
- **AND packs** = every subset up to Max AND size.
- **Tree leaves** = every yes/no path, in the order of your rule list.

```bash
python3 decision_tree.py --catalog bull,vol_up,px_gt_vwap --max-and 3
python3 decision_tree.py --rules "inside bar|high < prev high and low > prev low|filter"
```

Spreadsheet for testing : [OpenMe](https://docs.google.com/spreadsheets/d/1qutpCUrPPgjZY9ngcuFqp_Ryjx39BO0o20qQdc_Il6E/edit#gid=1002231824)

## FAQ
#### How to test apps script code on google spreadsheet
1. Take a new spreadsheet by visiting [Google Spreadsheet](scripts.google.com)
2. Open **Extension** option
3. Select **Apps Script**
4. Create a new "gs" file and paste your code into it.
