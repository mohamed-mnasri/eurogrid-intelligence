// Plain-language help for every page: what it is for, how to use it, what each section shows, and the words used.
// Shown by components/PageGuide.tsx. Keep sentences short and avoid jargon; explain any term that must stay.

export type Guide = {
  purpose: string
  steps: string[]
  sections: { name: string; what: string }[]
  terms?: { term: string; meaning: string }[]
}

export const GUIDES: Record<string, Guide> = {
  overview: {
    purpose: 'See what electricity cost and how much was used and produced in a country (or all of Europe) for one month.',
    steps: [
      'Pick a country (or All Europe), then a year and a month at the top.',
      'Read the four cards for the month in one glance: wholesale price, household price, grid use and generation.',
      'Click a month on the yearly chart to jump to it. Use the buttons on each chart to switch views.',
    ],
    sections: [
      { name: 'How to read the numbers', what: 'A short reminder of the units used on this page.' },
      { name: 'Country, year and month', what: 'Choose what to look at. Only months that have data can be picked.' },
      { name: 'The four cards', what: 'The month in numbers: average market price, the official household price, average power drawn from the grid, and how much was generated compared with what was used.' },
      { name: 'Wholesale vs household price', what: 'The green line is the market price each month; the pink line is what households pay including taxes and fees. The gap is roughly what fees and taxes add. Click a month to open it below.' },
      { name: 'Wholesale price detail', what: 'The market price hour by hour (or per day / week) in the chosen month, with the usual price for those hours as a dashed line.' },
      { name: 'Power needed from the public grid', what: 'How much power the whole country drew from the grid each hour. Switch between real GW and "% of the busiest hour".' },
      { name: 'Power generated compared with grid use', what: 'Pick a source (for example solar) to see what share of grid use it covered each hour. 100% means generation matched use.' },
      { name: 'Weather and solar/wind table', what: 'Temperature, wind and sunshine in a reference city next to the country\'s solar and wind output, per hour, day or week.' },
    ],
    terms: [
      { term: 'Wholesale price', meaning: 'The price on the electricity market before network fees and taxes, in € per MWh (÷ 1,000 = € per kWh).' },
      { term: 'GW / MWh / kWh', meaning: 'GW is power at one moment; MWh and kWh are energy over time. Household bills use kWh.' },
      { term: 'Stale data', meaning: 'The newest data is more than a week old, so the page is not live.' },
    ],
  },
  analytics: {
    purpose: 'Compare countries, see the typical shape of prices through the day in each month, and find unusual price hours.',
    steps: [
      'Choose a year and a focus market at the top.',
      'Compare countries on the first chart; the focus market is drawn thicker.',
      'Scroll to "Detected price anomalies". If it is empty, press Run detection. Click any dot or row to open its investigation.',
    ],
    sections: [
      { name: 'Monthly average price by country', what: 'One line per country: the average market price in each month.' },
      { name: 'Average price and price range', what: 'The yearly average per country, the cheapest and dearest single interval, and how often prices went below zero.' },
      { name: 'Typical day by month (heatmap)', what: 'Each square is the average price for one hour of the day in one month. Blue is cheap, orange is expensive. Midday dips usually come from solar power.' },
      { name: 'Detected price anomalies', what: 'Hours when the price was far from normal for that hour and day type. Bigger dots are more unusual. Click one to investigate it.' },
    ],
    terms: [
      { term: 'Anomaly', meaning: 'An hour whose price was much higher (spike) or lower (dip) than usual for the same hour over the previous 28 days.' },
      { term: 'Score', meaning: 'How unusual the price was. Above 5 counts as an anomaly; higher means more extreme.' },
    ],
  },
  investigations: {
    purpose: 'Find out what was happening when a price anomaly occurred, with evidence for every statement.',
    steps: [
      'Pick a case from the list on the left (filter by market or spike/dip if you like).',
      'Press "Run rule-based investigation" for an instant report, or "Run AI agent" to let a local AI model choose what to check (about 30–60 seconds; needs Ollama).',
      'Read the Findings. Click any grey evidence tag to see the exact data behind a statement.',
      'Open "Activity trace" to see each step the investigation took, and use Export to download the report.',
    ],
    sections: [
      { name: 'Case list', what: 'All detected anomalies, newest first, with their size and score.' },
      { name: 'Run buttons', what: 'Rule-based follows a fixed checklist. The AI agent decides which checks to run, but code checks every statement it writes before it is kept. "Replay for a household" shows what the event would have cost a home.' },
      { name: 'Findings', what: 'Observations (what was measured), relationships that changed at the same time, and plausible contributing factors. "Proven causes" is always empty on purpose: public data cannot prove why a price moved.' },
      { name: 'Missing evidence and confidence', what: 'What could not be checked (for example power-plant outages) and how complete each type of data was.' },
      { name: 'Activity trace', what: 'The path the investigation took, which tool was chosen by the model or by code, and, for the AI agent, how long it took and how many tokens it used.' },
      { name: 'Evidence', what: 'Every data lookup behind the report: its inputs, the tables it read and its result.' },
    ],
    terms: [
      { term: 'Evidence (ev_…)', meaning: 'A stored record of one data lookup. Every finding must point to at least one.' },
      { term: 'Complete / Limited / Blocked', meaning: 'Complete: the key data was there. Limited: some data was missing, so the report says less. Blocked: no price data, so the event cannot be checked.' },
      { term: 'Tokens', meaning: 'The units an AI model reads and writes. More tokens means more work for the model.' },
    ],
  },
  planner: {
    purpose: 'Estimate whether rooftop solar, with or without a battery, would pay off for a household.',
    steps: [
      'Choose a location and enter the yearly electricity use (a typical value is filled in).',
      'Pick the tariff. The import price is filled in from Eurostat; change it to your own price if you know it.',
      'Replace the example installation costs with a real quote if you have one, then press Run simulation.',
      'Compare the configurations in the table. Use "Choose by" to pick what matters most to you, and click a row to see its charts.',
    ],
    sections: [
      { name: 'Inputs', what: 'Location, yearly use, solar data source, tariff, export payment and finance settings.' },
      { name: 'Configurations', what: 'Three example systems (solar only, solar + small battery, larger solar + battery) with editable costs.' },
      { name: 'Configuration comparison', what: 'For each system: solar produced, how much of it the home used, the bill before and after, yearly savings, payback time and NPV (the long-term value in today\'s money).' },
      { name: 'Where the electricity comes from', what: 'Month by month: solar used directly, energy from the battery and energy bought from the grid.' },
      { name: 'Cumulative cash flow and sensitivity', what: 'When each system pays back, and how the result changes if costs, prices or sunshine are different.' },
      { name: 'Battery dispatch', what: 'One week hour by hour: solar, household use and when the battery charges and discharges. Move the slider to pick a week.' },
    ],
    terms: [
      { term: 'kWp', meaning: 'The size of a solar system (its peak power).' },
      { term: 'Payback', meaning: 'How many years until the savings cover the cost.' },
      { term: 'NPV', meaning: 'Net present value: total savings over the years, in today\'s money, minus the cost. Positive means it pays off.' },
    ],
  },
  replay: {
    purpose: 'See what one real price event would have cost a household, with and without solar and a battery.',
    steps: [
      'Run a simulation on the Renewable Planner first (it creates the household).',
      'Choose a market event and the household, then press Replay event.',
      'Read "What it meant for the household" for the headline, then the cost table for each configuration.',
    ],
    sections: [
      { name: 'What the market did', what: 'What was measured during the event, taken from its investigation report.' },
      { name: 'What it meant for the household', what: 'The extra cost of that day on a dynamic tariff (which follows market prices) and on the household\'s own tariff.' },
      { name: 'Cost of the replayed day', what: 'For each configuration: energy bought, battery use during the event, and the cost at actual prices versus usual prices. The difference is the event premium.' },
      { name: 'Charts', what: 'The price through the day (the event is shaded) and the household\'s use, solar output and grid purchases.' },
      { name: 'Assumptions', what: 'What the simulation assumed. It is an estimate, not a real bill.' },
    ],
    terms: [
      { term: 'Dynamic tariff', meaning: 'A price that changes every hour with the market. A fixed tariff stays the same, so a market spike does not change the bill.' },
      { term: 'Usual price', meaning: 'The typical price for that hour over the previous 28 days.' },
    ],
  },
  operations: {
    purpose: 'Check that the data is complete, fresh and passing its quality tests, and see what happens when a data feed breaks.',
    steps: [
      'Read the four cards at the top for overall health.',
      'Check "Pipeline runs" to see the latest data refresh and whether each step succeeded.',
      'Try Chaos mode: pick a fault and press Run chaos experiment to see which checks catch it (about 30 seconds; your real data is never changed).',
    ],
    sections: [
      { name: 'Health cards', what: 'Connectors working, data tests passed, how complete the data is, and the newest data date.' },
      { name: 'Pipeline runs', what: 'Each data refresh: fetch, clean, build and test, then detect anomalies. A failed step stops the run.' },
      { name: 'Chaos mode', what: 'Breaks a copy of the data on purpose (a missing or malformed feed) and shows whether the data tests, the quality check and the reports notice.' },
      { name: 'Coverage by country and domain', what: 'For each country and data type: complete, partial, stale or missing, and how many hours are missing.' },
      { name: 'Connectors and recent runs', what: 'Every request made to the data providers, with errors.' },
      { name: 'dbt tests, freshness and lineage', what: 'The automatic data tests and their results, whether the data is recent enough, and which tables would be affected if a source failed.' },
      { name: 'Source and licence register', what: 'Where each dataset comes from and its terms of use.' },
    ],
    terms: [
      { term: 'Pipeline', meaning: 'The automatic steps that fetch, clean and test the data.' },
      { term: 'dbt test', meaning: 'An automatic check on the data, such as "no duplicate rows" or "no missing hours".' },
      { term: 'Stale / Partial', meaning: 'Stale: no new data for over a week. Partial: some hours or values are missing.' },
    ],
  },
  lab: {
    purpose: 'See how well the investigators do on test cases where the right answer is known in advance.',
    steps: [
      'Read the two cards: how many test cases each investigator passes, and how long each investigation takes.',
      'Look at the matrix: a tick means a check passed, a cross means it failed. Click a case to see what the investigator did.',
      'Press "Run rule-based suite" to run the tests again (a few seconds).',
    ],
    sections: [
      { name: 'Result cards', what: 'Cases passed, how many runs were made, and the time and tokens per investigation.' },
      { name: 'Results by case and check', what: 'Seven made-up situations (for example a sudden drop in wind) scored on nine checks, such as "did it find the right cause signal" and "did it avoid claiming a cause".' },
      { name: 'The cases', what: 'What was injected into each test case and what the investigator should find.' },
      { name: 'Run history', what: 'Every test run so far, including earlier failures, so improvements can be compared over time.' },
    ],
    terms: [
      { term: 'Synthetic case', meaning: 'Invented data with one known condition, used for testing. It is not a real market event.' },
      { term: 'Rule-based vs AI agent', meaning: 'Rule-based follows fixed rules; the AI agent uses a local language model whose statements are checked by code.' },
    ],
  },
}
