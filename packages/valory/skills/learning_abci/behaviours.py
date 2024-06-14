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

"""This package contains round behaviours of LearningAbciApp."""

from abc import ABC
from typing import Generator, List, Set, Type, cast, Optional
from hexbytes import HexBytes


from packages.valory.protocols.contract_api import ContractApiMessage

from packages.valory.contracts.gnosis_safe.contract import (
    GnosisSafeContract,
    SafeOperation,
)

from packages.valory.contracts.multisend.contract import (
    MultiSendContract,
    MultiSendOperation,
)
from packages.valory.contracts.uniswap_v2_router_02.contract import (
    UniswapV2Router02Contract,
)
from packages.valory.contracts.uniswap_v2_erc20.contract import UniswapV2ERC20Contract
from packages.keyko.contracts.deposit_tracker.contract import DepositTracker

from packages.valory.skills.abstract_round_abci.base import AbstractRound
from packages.valory.skills.abstract_round_abci.behaviours import (
    AbstractRoundBehaviour,
    BaseBehaviour,
)
from packages.valory.skills.learning_abci.models import Params, SharedState
from packages.valory.skills.learning_abci.payloads import (
    APICheckPayload,
    DecisionMakingPayload,
    TxPreparationPayload,
)

from packages.valory.skills.transaction_settlement_abci.payload_tools import (
    hash_payload_to_hex,
)
from packages.valory.skills.learning_abci.rounds import (
    APICheckRound,
    DecisionMakingRound,
    LearningAbciApp,
    SynchronizedData,
    TxPreparationRound,
    Event
)

WaitableConditionType = Generator[None, None, bool]

GNOSIS_CHAIN_ID = "gnosis"
SAFE_GAS = 0

class LearningBaseBehaviour(BaseBehaviour, ABC):  # pylint: disable=too-many-ancestors
    """Base behaviour for the learning_abci skill."""

    @property
    def synchronized_data(self) -> SynchronizedData:
        """Return the synchronized data."""
        return cast(SynchronizedData, super().synchronized_data)

    @property
    def params(self) -> Params:
        """Return the params."""
        return cast(Params, super().params)

    @property
    def local_state(self) -> SharedState:
        """Return the state."""
        return cast(SharedState, self.context.state)


class APICheckBehaviour(LearningBaseBehaviour):  # pylint: disable=too-many-ancestors
    """APICheckBehaviour"""

    matching_round: Type[AbstractRound] = APICheckRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            sender = self.context.agent_address
            payload = APICheckPayload(sender=sender, price=1.0)

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()


class DecisionMakingBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """DecisionMakingBehaviour"""

    matching_round: Type[AbstractRound] = DecisionMakingRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            sender = self.context.agent_address
            evt = Event.TRANSACT.value
            payload = DecisionMakingPayload(sender=sender, event=evt)

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()
    

class TxPreparationBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """
    Behaviour to prepare and submit a multisend transaction for swapping WxDAI to WBTC and recording it in DepositTracker.

    Attributes:
        matching_round (Type[AbstractRound]): The matching round for this behaviour.
    """

    matching_round: Type[AbstractRound] = TxPreparationRound

    def async_act(self) -> Generator:
        """
        Execute the action.

        - Step 1: Retrieve the WxDAI balance from the safe.
        - Step 2: Prepare a deposit transaction if WxDAI balance is less than required.
        - Step 3: Prepare the swap transaction from WxDAI to WBTC.
        - Step 4: Prepare the transaction to log the swap in DepositTracker.
        - Step 5: Prepare the multisend transaction.
        - Step 6: Get the Safe transaction hash for the multisend transaction.
        - Step 7: Send the transaction payload.
        """
        
        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            # Step 1: Get the balance of WxDAI
            balance = yield from self._get_ERC20_balance(self.synchronized_data.safe_contract_address)
            self.context.logger.info(f"WxDAI Balance: {balance}")

            # Step 2: Prepare the deposit transaction if needed
            if balance is None or balance < self.params.trade_amount:
                deposit_amount = self.params.trade_amount - balance if balance else self.params.trade_amount
                deposit_txn = yield from self._prepare_deposit_tx(deposit_amount)
                self.context.logger.info(f"Deposit tx prepared: {deposit_txn}")

            # Step 3: Prepare the swap transaction
            swap_txn = yield from self._prepare_swap_tx()
            self.context.logger.info(f"Swap tx prepared: {swap_txn}")

            # Step 4: Prepare the DepositTracker transaction
            deposit_tracker_tx = yield from self._prepare_deposit_tracker_tx(swap_txn["amount_out"])
            self.context.logger.info(f"DepositTracker tx prepared: {deposit_tracker_tx}")

            # Step 5: Build the multisend transaction
            multi_send_txs = [deposit_txn, swap_txn, deposit_tracker_tx]

            multisend_data = yield from self._prepare_multisend_tx(multi_send_txs)
            self.context.logger.info(f"Multisend data prepared: {multisend_data}")

            # Step 6: Get the Safe transaction hash for the multisend transaction
            safe_tx_hash = yield from self._get_safe_tx_hash(multisend_data)
            self.context.logger.info(f"Safe tx hash: {safe_tx_hash}")

            # Step 7: Prepare the payload
            payload_string = hash_payload_to_hex(
                safe_tx_hash=safe_tx_hash,
                ether_value=0,
                safe_tx_gas=SAFE_GAS,
                to_address=self.synchronized_data.multisend_contract_address,
                data=bytes.fromhex(multisend_data),
                operation=SafeOperation.DELEGATE_CALL.value,
            )

            payload = TxPreparationPayload(
                sender=self.context.agent_address, tx_submitter=self.auto_behaviour_id(), tx_hash=payload_string
            )

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def _get_ERC20_balance(self, addr: str) -> Generator[None, None, Optional[int]]:
        """
        Retrieve the balance of WxDAI for a given address.

        :param addr: The address of the wallet.
        :return: The balance of WxDAI.
        """
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.params.wxdai_contract_address,
            contract_id=str(UniswapV2ERC20Contract.contract_id),
            contract_callable="check_balance",
            account=addr,
            chain_id=GNOSIS_CHAIN_ID
        )

        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(f"Could not get the balance: {response_msg}")
            return None

        return response_msg.state.body.get("balance", None)
    
    def _get_amounts_out(self, amount_in: int) -> Generator[None, None, int]:
        """
        Get the estimated output amount from Uniswap.

        :param amount_in: The amount of input tokens.
        :return: The estimated amount of output tokens.
        """
        path = [self.params.wxdai_contract_address, self.params.wbtc_contract_address]
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.CALL,
            contract_address=self.params.uniswap_router_address,
            contract_id=str(UniswapV2Router02Contract.contract_id),
            contract_callable="getAmountsOut",
            amount_in=amount_in,
            path=path,
        )
        if contract_api_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(f"Could not get amounts out: {contract_api_msg}")
            return 0  
        return contract_api_msg.state.body["amounts"][-1]
    
    def _prepare_deposit_tx(self, deposit_amount: int) -> Generator[None, None, dict]:
        """
        Prepare the deposit transaction to convert xDAI to WxDAI.

        :param deposit_amount: The amount of xDAI to deposit.
        :return: The prepared deposit transaction.
        """

        # Build the transaction data for the deposit operation on the ERC20 contract
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.params.wxdai_contract_address,
            contract_id=str(UniswapV2ERC20Contract.contract_id),
            contract_callable="build_deposit_tx",
            chain_id=GNOSIS_CHAIN_ID,
        )

        return {
            "operation": MultiSendOperation.CALL,
            "to": self.params.wxdai_contract_address,
            "value": deposit_amount,  # Send the deposit amount
            "data": HexBytes(contract_api_msg.raw_transaction.body["data"].hex()),
        }
    
    def _prepare_swap_tx(self) -> Generator[None, None, dict]:
        """
        Prepare the swap transaction on Uniswap.

        :return: The prepared swap transaction.
        """
        path = [self.params.wxdai_contract_address, self.params.wbtc_contract_address]
        deadline = int(self.context.now.timestamp() + 600)  # 10 minutes from now

        # Retrieve the estimated output amount
        estimated_out = yield from self._get_amounts_out(self.params.trade_amount, path)

        # Apply the slippage tolerance
        amount_out_min = int(estimated_out * (1 - self.params.slippage_tolerance))

        # Prepare the swap transaction
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.params.uniswap_router_address,
            contract_id=str(UniswapV2Router02Contract.contract_id),
            contract_callable="get_method_data",
            method_name="swapExactTokensForTokens",
            amount_in=self.params.trade_amount,
            amount_out_min=amount_out_min,
            path=path,
            to=self.synchronized_data.safe_contract_address,
            deadline=deadline,
        )
        swap_data = cast(bytes, contract_api_msg.raw_transaction.body["data"])

        return {
            "operation": MultiSendOperation.CALL,
            "to": self.params.uniswap_router_address,
            "value": 0,
            "data": HexBytes(swap_data.hex()),
        }
    
    def _prepare_deposit_tracker_tx(self, amount_swapped: int) -> Generator[None, None, dict]:
        """
        Prepare the transaction to log the swap in the DepositTracker.

        :param amount_swapped: The amount of tokens swapped.
        :return: The prepared DepositTracker transaction.
        """
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.params.deposit_tracker_address,
            contract_id=str(DepositTracker.contract_id),
            contract_callable="addLog",
            amount=amount_swapped,
            chain_id=GNOSIS_CHAIN_ID,
        )

        return {
            "operation": MultiSendOperation.CALL,
            "to": self.params.deposit_tracker_address,
            "value": 0,
            "data": HexBytes(contract_api_msg.raw_transaction.body["data"].hex()),
        }
    
    def _prepare_multisend_tx(self, txs: List[dict]) -> Generator[None, None, str]:
        """
        Prepare the multisend transaction.

        :param txs: The list of transactions to include in the multisend.
        :return: The prepared multisend data.
        """
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.synchronized_data.safe_contract_address,
            contract_id=str(MultiSendContract.contract_id),
            contract_callable="get_tx_data",
            multi_send_txs=txs,
        )

        multisend_data = contract_api_msg.raw_transaction.body["data"][2:]
        return multisend_data
    
    def _get_safe_tx_hash(self, multisend_data: str) -> Generator[None, None, str]:
        """
        Get the transaction hash from Gnosis Safe contract.

        :param multisend_data: The multisend transaction data.
        :return: The hash of the Safe transaction.
        """
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.synchronized_data.safe_contract_address,
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="get_raw_safe_transaction_hash",
            to_address=self.synchronized_data.multisend_contract_address,
            value=0,
            data=bytes.fromhex(multisend_data),
            operation=SafeOperation.DELEGATE_CALL.value,
            safe_tx_gas=SAFE_GAS,
            safe_nonce=self.synchronized_data.safe_nonce,
        )

        safe_tx_hash = contract_api_msg.raw_transaction.body["tx_hash"][2:]
        return safe_tx_hash

class LearningRoundBehaviour(AbstractRoundBehaviour):
    """LearningRoundBehaviour"""

    initial_behaviour_cls = APICheckBehaviour
    abci_app_cls = LearningAbciApp  # type: ignore
    behaviours: Set[Type[BaseBehaviour]] = [  # type: ignore
        APICheckBehaviour,
        DecisionMakingBehaviour,
        TxPreparationBehaviour,
    ]
