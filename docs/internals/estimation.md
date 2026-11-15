# Estimation

The estimate answers "how much space will I free, and how long will it take?" without converting everything. It test-encodes small samples of real files, then scales the result. Nothing beside the originals is created or changed, and its temporary files are deleted afterwards.

## Choosing the files

ClipShrink groups the selected files by resolution and rounded frame rate, sorts each group largest first, and orders the groups by total size. It then picks files round-robin across the groups, taking the next-largest file each round, until it has up to **5 files**. The number is fixed and not a setting.

## Sampling

Each picked file goes through the same search the real conversion uses (see [Quality](quality.md)):

- 3 pieces of 4 seconds, copied from 25%, 50% and 75% of the file, or the whole file if it is 18 seconds or shorter;
- the format, encoder, largest size and quality values chosen in Settings;
- a binary search over the encoder's 9 quality settings, starting from the previous file's result for the same group.

The samples at the chosen setting are what the estimate uses. If no setting meets the target, the strictest one is used, because a below-target file is still converted and still saves space.

## The formula

1. **Ratio per file:** encoded sample bytes divided by source sample bytes, capped at **1.0**. The estimate never predicts growth, and refusals for "not smaller" are not modelled.
2. **Ratio per group:** the average of its files' ratios. A group with no sampled file uses the average of all ratios.
3. **Expected saving:** the sum over all selected files of `video bytes × (1 − group ratio)`. Only video bytes count; audio and other tracks are not part of the saving.
4. **Range:** the low end uses the highest sampled file ratio in the file's group (or among all samples if the group has none), raised by 20% and capped at 1.0; the high end uses the lowest such ratio, reduced by 20%. The 20% applies to the ratio, not to the saving. The expected value always lies inside it, and the high end cannot exceed the total size.
5. **Time:** every FFmpeg run of the test encodes reports two things: how long it took to get going (opening the file, the encoder and the VMAF models) and its steady speed once running. A 4-second sample is mostly start-up, so the two are kept apart. The steady speed becomes seconds per megapixel per second of video, measured separately for each resolution and frame rate the samples cover. Each file then counts its own quality search (as many tries as the samples needed on average, each on 3 four-second pieces, or on the whole file if it's 18 seconds or shorter) and one full encode plus its VMAF check, each with one start-up.

The headline in the app reads "About {saved} saved ({percent}%)", followed by the likely range and the time.

!!! note "Limits"
    Samples are short pieces, so unusual scenes elsewhere in a file can change the real result. Retries at stricter settings, and the few seconds of reading and writing each file, are not counted. Up to 1.7.2 the time was the samples' total time scaled to the whole selection, which counted start-up for every second of video: on two real batches it came out about 3.7 times too long. Replayed on the same two batches, the new method gave 3.54 h for a batch that took 3.45 h, and 0.72 h for one that took 0.91 h.

## Accuracy check

The project's test suite checks that on three generated clips the estimate lands within 35% of the real saving, and that the low end is not above the expected value, which is not above the high end, which is not above the total.

## Next steps

- [Reading the estimate in the app](../user-guide/estimates.md)
- [How quality is measured](quality.md)
