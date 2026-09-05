"""JRA frame (waku) metadata used only for runner presentation."""


def jra_frame_number(horse_number: int, runner_count: int) -> int:
    """Return the official JRA frame for a horse-number/field-size pair.

    JRA fills inner frames first for fields below 16 and adds the 17th and
    18th runners to the two outer frames.  This is useful for verified legacy
    cards captured before the parser persisted the explicit ``waku`` cell.
    """
    if type(runner_count) is not int or not 2 <= runner_count <= 18:
        raise ValueError("runner_count must be an integer from 2 to 18")
    if type(horse_number) is not int or not 1 <= horse_number <= runner_count:
        raise ValueError("horse_number must be within the field")
    if runner_count <= 8:
        return horse_number
    if runner_count <= 16:
        single_frames = 16 - runner_count
        return horse_number if horse_number <= single_frames else (
            single_frames + (horse_number - single_frames + 1) // 2
        )
    if runner_count == 17:
        return min(8, (horse_number + 1) // 2)
    if horse_number <= 12:
        return (horse_number + 1) // 2
    return 7 if horse_number <= 15 else 8
