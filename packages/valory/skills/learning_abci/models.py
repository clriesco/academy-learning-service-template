# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2024 Valory AG
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

"""This module contains the shared state for the abci skill of LearningAbciApp."""

from typing import Any

from packages.valory.skills.abstract_round_abci.models import BaseParams
from packages.valory.skills.abstract_round_abci.models import (
    BenchmarkTool as BaseBenchmarkTool,
)
from packages.valory.skills.abstract_round_abci.models import Requests as BaseRequests
from packages.valory.skills.abstract_round_abci.models import (
    SharedState as BaseSharedState,
)
from packages.valory.skills.learning_abci.rounds import LearningAbciApp


class SharedState(BaseSharedState):
    """Keep the current shared state of the skill."""

    abci_app_cls = LearningAbciApp


Requests = BaseRequests
BenchmarkTool = BaseBenchmarkTool


class Params(BaseParams):
    """Parameters."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the parameters object."""
        self.coingecko_price_template = self._ensure(
            "coingecko_price_template", kwargs, str
        )
        self.coingecko_api_key = kwargs.get("coingecko_api_key", None)
        self.transfer_target_address = self._ensure(
            "transfer_target_address", kwargs, str
        )
        self.erc20_token_address = self._ensure("erc20_token_address", kwargs, str)
        self.fxstreet_api_url = self._ensure("fxstreet_api_url", kwargs, str)
        self.usbls_statement_page = self._ensure(
            "usbls_statement_page", kwargs, str
        )

        self.wxdai_contract_address = self._ensure("wxdai_contract_address", kwargs, str)
        self.wbtc_contract_address = self._ensure("wbtc_contract_address", kwargs, str)

        self.trade_amount = self._ensure("trade_amount", kwargs, int)
        self.uniswap_router_address = self._ensure(
            "uniswap_router_address", kwargs, str
        )
        self.slippage_tolerance = self._ensure("slippage_tolerance", kwargs, float)

        self.deposit_tracker_address = self._ensure(
            "deposit_tracker_address", kwargs, str
        )
        self.multisend_contract_address = self._ensure(
            "multisend_contract_address", kwargs, str
        )

        super().__init__(*args, **kwargs)
