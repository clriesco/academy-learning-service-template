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
from aea.exceptions import AEAEnforceError
from typing import Generator, Set, Type, cast, Optional, Any

from packages.valory.contracts.erc20.contract import ERC20

from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.protocols.ledger_api import LedgerApiMessage
from packages.valory.contracts.gnosis_safe.contract import GnosisSafeContract

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
from packages.valory.skills.transaction_settlement_abci.rounds import TX_HASH_LENGTH

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
import json


WaitableConditionType = Generator[None, None, bool]

HTTP_OK = 200
GNOSIS_CHAIN_ID = "gnosis"
TX_DATA = b"0x"
SAFE_GAS = 0
VALUE_KEY = "value"
TO_ADDRESS_KEY = "to_address"


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
            price_response = yield from self.get_data()
            self.context.logger.info(
                f"Received price autonolas/usd from Coingecko API: {price_response}"
            )
            sender = self.context.agent_address
            payload = APICheckPayload(sender=sender, price=float(price_response))

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def get_data(self) -> Generator[None, None, str]:
        """
        Get the data from Coingecko API.

        :yield: HttpMessage object
        :return: return the data retrieved from Coingecko API, in case something goes wrong we return "{}".
        """
        response = yield from self.get_http_response(
            method="GET",
            url=self.params.coingecko_price_template,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-cg-demo-api-key": self.params.coingecko_api_key,
            },
        )
        if response.status_code != 200:
            self.context.logger.error(
                f"Could not retrieve data from Coingecko APIs."
                f"Received status code {response.status_code}."
            )
            return "NaN"

        try:
            response_body = json.loads(response.body)

        except (ValueError, TypeError) as e:
            self.context.logger.error(
                f"Could not parse response from coingecko API, "
                f"the following error was encountered {type(e).__name__}: {e}"
            )
            return "NaN"
        except Exception as e:  # pylint: disable=broad-except
            self.context.logger.error(
                f"An unexpected error was encountered while parsing the coingecko API response "
                f"{type(e).__name__}: {e}"
            )
            return "NaN"
        
        return response_body["autonolas"]["usd"]


class DecisionMakingBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """DecisionMakingBehaviour"""

    matching_round: Type[AbstractRound] = DecisionMakingRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            decision = self.get_decision()

            erc20_balance = yield from self._get_ERC20_balance(self.synchronized_data.safe_contract_address)
            self.context.logger.info(f"ERC20 Balance of address {self.synchronized_data.safe_contract_address}: {erc20_balance}")

            balance = yield from self._get_balance(self.synchronized_data.safe_contract_address)
            self.context.logger.info(f"Balance of address {self.synchronized_data.safe_contract_address}: {balance}")

            sender = self.context.agent_address
            payload = DecisionMakingPayload(sender=sender, event=decision)

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()
    
    def get_decision(self) -> str:
        """ 
        Check if the price is above or below 3
        
        If the price is above the threshold, we return "TRANSACT".
        If the price is below the threshold, we return "DONE".

        :return: the decision payload.
        """
        price = self.synchronized_data.price
        self.context.logger.info('Checking if price is within threshold...')
        if price > 3 or price < 2:
            self.context.logger.info(
                f"Price is outside the threshold. Price: {price} WEI."
            )
            return Event.TRANSACT.value
        self.context.logger.info(
            f"Price is within the threshold. Price: {price} WEI."
        )
        return Event.TRANSACT.value
    
    def _get_ERC20_balance(self, addr: str) -> Generator[None, None, Optional[int]]:
        """Get the given safe's balance."""    

        self.context.logger.info(
            f"Checking erc20 balance for address {addr} in token {self.params.erc20_token_address} for contract {ERC20.contract_id}..."
        )
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.params.erc20_token_address,
            contract_id=str(ERC20.contract_id),
            contract_callable="check_balance",
            account=addr,
            chain_id=GNOSIS_CHAIN_ID
        )
        self.context.logger.info(response_msg)
        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(
                f"Could not calculate the erc20 balance of the address: {response_msg}"
            )
            return 

        token = response_msg.state.body.get("token", None)
        wallet = response_msg.state.body.get("wallet", None)
        if token is None or wallet is None:
            self.context.logger.error(
                f"Something went wrong while trying to get the erc20 balance of the address: {response_msg}"
            )
            return None

        self.context.logger.info(f"The safe {self.synchronized_data.safe_contract_address} has {wallet} wei xDAI and {token} wei WxDAI.")
        return token
    
    def _get_balance(self, addr: str) -> Generator[None, None, Optional[int]]:
        """Get the given safe's balance."""
        self.context.logger.info(f"Checking balance for address {addr}...")
        ledger_api_response = yield from self.get_ledger_api_response(
            performative=LedgerApiMessage.Performative.GET_STATE,  # type: ignore
            ledger_callable="get_balance",
            account=addr,
            chain_id=GNOSIS_CHAIN_ID
        )

        try:
            balance = int(ledger_api_response.state.body["get_balance_result"])
        except (AEAEnforceError, KeyError, ValueError, TypeError):
            balance = None

        if balance is None:
            log_msg = f"Failed to get the balance for address {addr}."
            self.context.logger.error(f"{log_msg}: {ledger_api_response}")
            return None

        return balance

class TxPreparationBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """TxPreparationBehaviour"""

    matching_round: Type[AbstractRound] = TxPreparationRound
    ETHER_VALUE = 10  # 10 WEI

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            tx_data = yield from self.get_tx()
            sender = self.context.agent_address
            payload = TxPreparationPayload(
                sender=sender, tx_submitter=self.auto_behaviour_id(), tx_hash=tx_data
            )

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def get_tx_hash(self) -> Generator[None, None, Optional[str]]:
        """Get the transaction hash"""
        # Send 1 wei to the agent
        call_data = {VALUE_KEY: self.ETHER_VALUE, TO_ADDRESS_KEY: self.params.transfer_target_address}
        self.context.logger.info(f"Preparing the transaction hash for the transfer tx: {call_data}")
        safe_tx_hash = yield from self._build_safe_tx_hash(**call_data)
        if safe_tx_hash is None:
            self.context.logger.error("Could not build the safe transaction's hash.")
            return None

        tx_hash = hash_payload_to_hex(
            safe_tx_hash,
            call_data[VALUE_KEY],
            SAFE_GAS,
            call_data[TO_ADDRESS_KEY],
            TX_DATA,
        )

        self.context.logger.info(f"Transaction hash is {tx_hash}")

        return tx_hash

    def _build_safe_tx_hash(
        self, **kwargs: Any
    ) -> Generator[None, None, Optional[str]]:
        """Prepares and returns the safe tx hash for a multisend tx."""

        self.context.logger.info(
            f"Preparing Transfer transaction for Safe [{self.synchronized_data.safe_contract_address}]: {kwargs}"
        )

        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.synchronized_data.safe_contract_address,
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="get_raw_safe_transaction_hash",
            data=TX_DATA,
            safe_tx_gas=SAFE_GAS,
            chain_id=GNOSIS_CHAIN_ID,
            **kwargs,
        )

        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(
                "Couldn't get safe tx hash. Expected response performative "
                f"{ContractApiMessage.Performative.STATE.value!r}, "  # type: ignore
                f"received {response_msg.performative.value!r}: {response_msg}."
            )
            return None

        tx_hash = response_msg.state.body.get("tx_hash", None)
        if tx_hash is None or len(tx_hash) != TX_HASH_LENGTH:
            self.context.logger.error(
                "Something went wrong while trying to get the buy transaction's hash. "
                f"Invalid hash {tx_hash!r} was returned."
            )
            return None

        # strip "0x" from the response hash
        return tx_hash[2:]

    def get_tx(self) -> Generator[None, None, str]:
        """
        Prepares a safe tx and returns it.
        """
        deposit_tx_data = yield from self._get_deposit_tx()
        if deposit_tx_data is None:
            self.context.logger.error("Could not prepare the deposit tx.")
            return DecisionMakingRound.ERROR_PAYLOAD
    
        safe_tx_hash = yield from self._get_safe_tx_hash(deposit_tx_data)
        if safe_tx_hash is None:
            self.context.logger.error("Could not prepare the safe tx hash.")
            return DecisionMakingRound.ERROR_PAYLOAD
        
        payload_data = hash_payload_to_hex(
            safe_tx_hash=safe_tx_hash,
            ether_value=self.ETHER_VALUE,
            safe_tx_gas=SAFE_GAS,
            to_address=self.params.erc20_token_address,
            data=deposit_tx_data
        )

        return payload_data
    
    def _get_transfer_tx(self) -> Generator[None, None, str]:
        """
        Prepare the transfer tx.
        """
        self.context.logger.info("Preparing the transfer tx...")
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.erc20_token_address,
            contract_id=str(ERC20.contract_id),
            contract_callable="build_transfer_tx",
            chain_id=GNOSIS_CHAIN_ID,
            to_address=self.params.transfer_target_address,
            amount=self.ETHER_VALUE
        )

        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"Could not prepare the transfer tx: {response_msg}"
            )
            return None

        transfer_tx_data = response_msg.raw_transaction.body.get("data", None)
        if transfer_tx_data is None:
            self.context.logger.error(
                f"Something went wrong while trying to prepare the transfer tx: {response_msg}"
            )
            return None
        self.context.logger.info(f"Transfer tx prepared: {transfer_tx_data}")

        return transfer_tx_data
    
    
    def _get_deposit_tx(self) -> Generator[None, None, Optional[bytes]]:
        """
        Prepare the deposit tx.
        """
        self.context.logger.info("Preparing the deposit tx...")
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.erc20_token_address,
            contract_id=str(ERC20.contract_id),
            contract_callable="build_deposit_tx",
            chain_id=GNOSIS_CHAIN_ID,
        )

        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"Could not prepare the deposit tx: {response_msg}"
            )
            return None

        deposit_tx_data = response_msg.raw_transaction.body.get("data", None)
        if deposit_tx_data is None:
            self.context.logger.error(
                f"Something went wrong while trying to prepare the deposit tx: {response_msg}"
            )
            return None
        self.context.logger.info(f"Deposit tx prepared: {deposit_tx_data}")

        return deposit_tx_data
    
    def _get_safe_tx_hash(self, data: bytes) -> Generator[None, None, Optional[str]]:
        """
        Get the safe tx hash.
        """
        self.context.logger.info(f"Preparing the safe tx hash... with safe contract address {self.synchronized_data.safe_contract_address}")
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.synchronized_data.safe_contract_address,  # the safe contract address
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="get_raw_safe_transaction_hash",
            to_address=self.params.erc20_token_address,
            value=self.ETHER_VALUE, # TODO: ASK
            data=data,
            safe_tx_gas=SAFE_GAS,
            chain_id=GNOSIS_CHAIN_ID
        )

        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(
                f"Couldn't get safe hash. "
                f"Expected response performative {ContractApiMessage.Performative.STATE.value}, "  # type: ignore
                f"received {response_msg.performative.value}."
            )
            return None

        # strip "0x" from the response hash
        self.context.logger.info(f"Safe tx hash prepared: {response_msg.state.body['tx_hash']}")
        tx_hash = cast(str, response_msg.state.body["tx_hash"])[2:]
        return tx_hash

class LearningRoundBehaviour(AbstractRoundBehaviour):
    """LearningRoundBehaviour"""

    initial_behaviour_cls = APICheckBehaviour
    abci_app_cls = LearningAbciApp  # type: ignore
    behaviours: Set[Type[BaseBehaviour]] = [  # type: ignore
        APICheckBehaviour,
        DecisionMakingBehaviour,
        TxPreparationBehaviour,
    ]
