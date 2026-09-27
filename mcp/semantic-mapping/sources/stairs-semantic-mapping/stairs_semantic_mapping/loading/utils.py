import argparse
from pathlib import Path


def  parse_args_for_data_preparation() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare data with embeddings of standard entities for loading to PostgreSQL DB"
    )
    parser.add_argument(
        "--data-files",
        nargs='*',
        type=Path,
        required=True,
        help="Path to input csv files with raw data",
    )
    parser.add_argument(
        "--save-to",
        type=Path,
        help="Path to save the resulted file with embeddings",
    )
    return parser.parse_args()


def parse_args_for_db_loading() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load standard data into PostgreSQL database"
    )
    parser.add_argument(
        "--data-file",
        type=Path,
        required=True,
        help="Path to the input file with data for loading.",
    )
    parser.add_argument(
        "--create-tables",
        type=Path,
        help="Drop and create target tables before loading data",
    )
    return parser.parse_args()
