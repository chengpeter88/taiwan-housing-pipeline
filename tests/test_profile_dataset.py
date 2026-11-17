import pandas as pd

from scripts.profile_dataset import HOUSING, WITH_PARKING, parking_status, roc_dates


def test_roc_dates_do_not_normalize_invalid_days():
    dates = roc_dates(pd.Series(['1130229', '1140230', '', '1140715'], dtype='string'))
    assert dates.iloc[0] == pd.Timestamp('2024-02-29')
    assert dates.iloc[1:3].isna().all()
    assert dates.iloc[3] == pd.Timestamp('2025-07-15')


def test_parking_zero_is_not_automatically_no_parking():
    df = pd.DataFrame({
        '交易標的': [HOUSING, WITH_PARKING, WITH_PARKING, WITH_PARKING, WITH_PARKING, HOUSING],
        '車位總價元': ['0', '0', '100', '0', '100', '0'],
        '車位移轉總面積平方公尺': ['0', '0', '0', '10', '10', '0'],
        '車位類別': ['', '坡道平面', '坡道平面', '坡道平面', '坡道平面', '坡道平面'],
    }, dtype='string')
    assert parking_status(df).tolist() == [
        'no_parking', 'unclear_or_inconsistent', 'unclear_or_inconsistent',
        'unclear_or_inconsistent', 'separately_reported', 'unclear_or_inconsistent',
    ]
