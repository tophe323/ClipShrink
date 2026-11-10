# Reports

ClipShrink keeps a CSV report for each batch, for your information.

## What the automatic report contains

A report is written automatically while a batch runs. After each finished file, the batch's report is rewritten with every file finished so far in that run. If a report can't be written, the conversion carries on regardless.

- A batch where nothing finished writes no report.
- A report covers only the files finished in that run.

The report is a CSV file named `report_YYYY-MM-DD_HHMMSS.csv` (with `_2`, `_3` and so on if that name exists), with one row per file and these columns:

| Column | Meaning |
|---|---|
| Started, Finished, Time taken | When the file's conversion began and ended, and how long it took (hours:minutes:seconds) |
| File, Folder | Which video |
| Result | For example "Verified", "Resized", "⚠ Below target", "Original kept" |
| Reason | Why a file was kept or warned about |
| Before (bytes), After (bytes) | Sizes of the original and the conversion |
| Saved % | `1 - after / before`, to one decimal |
| VMAF average, VMAF worst 1% | The measured scores, to two decimals |
| Encoder, Quality setting | What was used |
| Conversion | Path of the converted file |
| Codec, Output resolution, Output bitrate (Mbit/s) | The format, size and bitrate of the conversion |
| Target average, Target worst 1% | The values this file was judged by. See [Advanced settings](advanced-settings.md). |

After, Saved % and Conversion are filled in only for files that were converted.

Under the table, after an empty line, a short summary of the batch:

| Line | Meaning |
|---|---|
| Batch started, Last file finished | When the batch began, and when its last finished file ended |
| Total time | The time between the two, pauses included |
| Encoder | What converted the files, for example "nvenc (NVIDIA)" or "x265 (processor)" |
| Graphics card, Processor, Windows | What the PC is, by name only |
| ClipShrink | The version that wrote the report |

## Opening a report in Excel

Reports use your PC's own list separator and decimal symbol, from Windows' regional settings. On a PC set to French, Dutch or German, for example, columns are separated by `;` and decimals use a comma, so a double-click opens the report in Excel with every value in its own column. Text that contains a comma or a semicolon, such as a file name or a reason, is put in quotes, as CSV requires.

Reports from 1.7 and earlier always used commas. On a PC that expects `;`, open them in Excel with **Data** > **From Text/CSV** and choose **Comma** as the delimiter.

## Where reports are stored

```text
%LOCALAPPDATA%\ClipShrink\reports
```

The **About** column on the Settings page has a link that opens this folder in Explorer. Reports are written in the app's language at the time. CSV files open in Excel and most spreadsheet apps. Reports stay on your PC until you delete them yourself.

## The app doesn't read them

Reports are only for your information. ClipShrink writes them and never reads them back, so you can move or delete them freely. They stay when you exit the app or click **Start Over**.

## Next steps

- [Compare and clean up](compare-and-clean-up.md)
- [Settings and data](settings-and-data.md)
