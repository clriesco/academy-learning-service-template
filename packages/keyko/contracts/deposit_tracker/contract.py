# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2024 Keyko AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------
"""This module contains the class to connect to the DepositTracker contract."""

from typing import Dict, List

from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea.crypto.base import LedgerApi
from aea_ledger_ethereum import EthereumApi


PUBLIC_ID = PublicId.from_str("keyko/deposit_tracker:0.1.0")


class DepositTracker(Contract):
    """The DepositTracker contract."""

    contract_id = PUBLIC_ID

    @classmethod
    def add_log(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        amount: int,
        sender_address: str,
    ) -> Dict[str, bytes]:
        """Build a transaction to add a log."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        data = contract_instance.encodeABI("addLog", args=(amount,))
        transaction = {
            "to": contract_address,
            "data": bytes.fromhex(data[2:]),
            "from": sender_address,
        }
        return transaction

    @classmethod
    def get_deposits(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        account: str,
    ) -> Dict[str, List[int]]:
        """Get the list of deposits for a given account."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        result = contract_instance.functions.getDeposits(account).call()
        amounts = result[0]
        block_numbers = result[1]
        return {"amounts": amounts, "block_numbers": block_numbers}
