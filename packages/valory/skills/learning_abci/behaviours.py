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
from datetime import datetime, timedelta
from typing import Generator, List, Set, Type, cast, Optional
from hexbytes import HexBytes
import re
import json
from bs4 import BeautifulSoup

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
from packages.valory.contracts.erc20.contract import ERC20
from packages.keyko.contracts.deposit_tracker.contract import DepositTracker

from packages.valory.skills.abstract_round_abci.base import AbstractRound
from packages.valory.skills.abstract_round_abci.io_.store import SupportedFiletype
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
            new_round_count = self.synchronized_data.round_number + 1

            self.context.logger.info(
                f"APICheckBehaviour: Starting the API check for round {new_round_count}."
            )

            bls_response = yield from self.get_bls_data()
            bls_html_text = BeautifulSoup(bls_response, 'html.parser').select_one('#bodytext .normalnews').text

            index_value = self.get_index_value(bls_html_text)
            consensus_value = yield from self.get_fxstreet_data()

            self.context.logger.info(
                f"APICheckBehaviour: Index value: {index_value}, Consensus value: {consensus_value}"
            )

            ipfs_hash = yield from self.send_to_ipfs(
                "bls_data.json",
                {
                    "bls_text": bls_html_text,    
                },
                filetype=SupportedFiletype.JSON,
                timeout=10.0
            )

            different_hash = self.synchronized_data.hash_value != ipfs_hash if self.synchronized_data.hash_value else False

            self.context.logger.info(
                f"APICheckBehaviour: IPFS hash: {ipfs_hash}, prev IPFS hash: {self.synchronized_data.hash_value},  Different hash: {different_hash}"
            )

            sender = self.context.agent_address
            payload = APICheckPayload(
                sender=sender, 
                bls_index_value=index_value,
                consensus_value=consensus_value,
                hash_value=ipfs_hash,
                different_hash=different_hash,
                round_number=new_round_count
            )

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()
    
    def get_fxstreet_data(self) -> Generator[None, None, str]:
        """
        Get the data from FXStreet.com which contains the consensus value needed
        """

        begin_of_current_month = datetime.now().replace(day=1).strftime("%Y-%m-%dT00:00:00Z")
        end_of_current_month = (datetime.now().replace(day=1) + timedelta(days=32)).strftime("%Y-%m-%dT23:59:59Z")
        url = self.params.fxstreet_api_url + f"{begin_of_current_month}/{end_of_current_month}"
        response = yield from self.get_http_response(
            method="GET",
            url=url,
            
            parameters={
                "volatilities": "HIGH",
                "countries": "US",
                "categories": "33303F5E-1E3C-4016-AB2D-AC87E98F57CA"
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/",
                "Referer": "https://www.fxstreet.com/",
            }
        )
        if response.status_code != 200:
            self.context.logger.error(
                f"APICheckBehaviour: url: {url}"
                f"APICheckBehaviour: Could not retrieve data from fxstreet. "
                f"APICheckBehaviour: Received status code {response.status_code}."
            )
            self.context.logger.error(f"APICheckBehaviour: Response body: {response.body}")
            self.context.logger.error(f"APICheckBehaviour: Response headers: {response.headers}")
            return ""
        
        data = json.loads(response.body)
        
        event_id = "6f846eaa-9a12-43ab-930d-f059069c6646"
        consensus_value = None

        if isinstance(data, dict):
            return None

        for item in data:
            if item["eventId"] == event_id:
                consensus_value = item["consensus"]
                break

        if consensus_value is not None:
            return consensus_value
        else:
            return None
        
    def get_bls_data(self) -> Generator[None, None, str]:
        """
        Get the data from BLS.gov which contains the index value needed
        """
        self.context.logger.info(
            f"APICheckBehaviour: Getting data from BLS. URL: {self.params.usbls_statement_page}"
        )
        response = yield from self.get_http_response(
            method="GET",
            url=self.params.usbls_statement_page,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "ngrok-skip-browser-warning": "1"
            },
        )
        if response.status_code != 200:
            self.context.logger.error(
                f"APICheckBehaviour: Could not retrieve data from BLS."
                f"APICheckBehaviour: Received status code {response.status_code}."
            )
            self.context.logger.error(f"APICheckBehaviour: Response body: {response.body}")
            self.context.logger.error(f"APICheckBehaviour: Response headers: {response.headers}")
            return ""
        
        html_content = response.body.decode('utf-8')
        return html_content

    def get_index_value(self, html_response: str) -> Optional[float]:
        """
        Get the index value from the html text
        """
        pattern = r'the all items index (increased|raised|decreased|went down).*?(\d+(\.\d+)?) percent'
        match = re.search(pattern, html_response, re.IGNORECASE)

        if match:
            # Capture the trend direction and the numeric value
            trend = match.group(1).lower()
            value = float(match.group(2))
            
            # Determine if the trend is negative
            if trend in ['decreased', 'down']:
                value = -value
            
            return value

        return None


class DecisionMakingBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """DecisionMakingBehaviour"""

    matching_round: Type[AbstractRound] = DecisionMakingRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            decision = self.get_decision(
                self.synchronized_data.consensus_value,
                self.synchronized_data.bls_index_value,
                self.synchronized_data.different_hash
            )
            
            self.context.logger.info(
                f"DecisionMakingBehaviour: Round decision: {decision}"
            )
            
            sender = self.context.agent_address
            payload = DecisionMakingPayload(sender=sender, event=decision)

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()
    
    def get_decision(self, consensus_value: float, index_value: float, different_hash: bool) -> str:
        """ 
        Check if the consensus value is higher than the index value and if there's a new release from BLS.
        
        :return: the decision payload.
        """
        self.context.logger.info('Checking if price is within threshold...')
        if (consensus_value is None or index_value is None or different_hash is None):
            self.context.logger.error(
                f"DecisionMakingBehaviour: Could not get the consensus value, index value or different hash value."
            )
            return Event.ERROR.value
        
        if (consensus_value > index_value and different_hash is True):
            self.context.logger.info(
                f"DecisionMakingBehaviour: Index value is lower than consensus value and there's a new release from BLS. Consensus value: {consensus_value}, Index value: {index_value}. New statement release: {different_hash}."
            )
            return Event.TRANSACT.value
        
        self.context.logger.info(
            f"DecisionMakingBehaviour: Index value is higher than consensus value or there's not a new release from BLS from the last decision making. Consensus value: {consensus_value}, Index value: {index_value}."
        )
        return Event.DONE.value

    

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
        - Step 3: Prepare the approve transaction for swapping WxDAI.
        - Step 4: Prepare the swap transaction from WxDAI to WBTC.
        - Step 5: Prepare the transaction to log the swap in DepositTracker.
        - Step 6: Prepare the multisend transaction.
        - Step 7: Get the Safe transaction hash for the multisend transaction.
        - Step 8: Send the transaction payload.
        """
        
        with self.context.benchmark_tool.measure(self.behaviour_id).local():

            multi_send_txs = []

            # Step 1: Get the balance of WxDAI
            balance = yield from self._get_ERC20_balance(self.synchronized_data.safe_contract_address, self.params.wxdai_contract_address)
            self.context.logger.info(f"WxDAI Balance: {balance}")
            wbtc_balance = yield from self._get_ERC20_balance(self.synchronized_data.safe_contract_address, self.params.wbtc_contract_address)
            self.context.logger.info(f"WBTC Balance: {wbtc_balance}")

            # Step 2: Prepare the deposit transaction if needed
            if balance is None or balance < self.params.trade_amount:
                deposit_amount = self.params.trade_amount - balance if balance else self.params.trade_amount
                deposit_txn = yield from self._prepare_deposit_tx(deposit_amount)
                self.context.logger.info(f"Deposit tx prepared: {deposit_txn}")
                multi_send_txs.append(deposit_txn)

            # Step 3: Prepare the approve transaction
            approve_txn = yield from self._prepare_approve_tx(self.params.wxdai_contract_address, self.params.trade_amount)
            self.context.logger.info(f"Approve tx prepared: {approve_txn}")
            multi_send_txs.append(approve_txn)

            # Step 4: Prepare the swap transaction
            # Retrieve the estimated output amount
            estimated_out = yield from self._get_amounts_out(self.params.trade_amount)
            self.context.logger.info(f"Estimated out: {estimated_out}")
            # Apply the slippage tolerance
            amount_out_min = int(estimated_out * (1 - self.params.slippage_tolerance))

            swap_txn = yield from self._prepare_swap_tx(amount_out_min)
            self.context.logger.info(f"Swap tx prepared: {swap_txn}")
            multi_send_txs.append(swap_txn)

            # Step 5: Prepare the DepositTracker transaction
            deposit_tracker_tx = yield from self._prepare_deposit_tracker_tx(amount_out_min)
            self.context.logger.info(f"DepositTracker tx prepared: {deposit_tracker_tx}")
            multi_send_txs.append(deposit_tracker_tx)

            # Step 6: Build the multisend transaction
            multisend_data = yield from self._prepare_multisend_tx(multi_send_txs)
            self.context.logger.info(f"Multisend data prepared: {multisend_data}")

            # Step 7: Get the Safe transaction hash for the multisend transaction
            safe_tx_hash = yield from self._get_safe_tx_hash(multisend_data)
            self.context.logger.info(f"Safe tx hash: {safe_tx_hash}")

            # Step 8: Prepare the payload
            payload_string = hash_payload_to_hex(
                safe_tx_hash=safe_tx_hash,
                ether_value=0,
                safe_tx_gas=SAFE_GAS,
                to_address=self.params.multisend_contract_address,
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

    def _get_ERC20_balance(self, addr: str, contract_address) -> Generator[None, None, Optional[int]]:
        """
        Retrieve the balance of WxDAI for a given address.

        :param addr: The address of the wallet.
        :return: The balance of WxDAI.
        """
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=contract_address,
            contract_id=str(ERC20.contract_id),
            contract_callable="check_balance",
            account=addr,
            chain_id=GNOSIS_CHAIN_ID
        )

        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(f"Could not get the balance: {response_msg}")
            return None

        return response_msg.state.body.get("token", None)
    
    def _prepare_approve_tx(self, token_address: str, amount: int) -> Generator[None, None, dict]:
        """
        Prepare the approval transaction for a given token.

        :param token_address: The address of the token contract.
        :param amount: The amount to approve.
        :return: The prepared approval transaction.
        """

        # Build the transaction data for the approval operation on the ERC20 contract
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=token_address,
            contract_id=str(ERC20.contract_id),
            contract_callable="build_approval_tx",
            spender=self.params.uniswap_router_address,
            amount=amount,
            chain_id=GNOSIS_CHAIN_ID,
        )

        approve_data = cast(bytes, contract_api_msg.raw_transaction.body["data"])

        return {
            "operation": MultiSendOperation.CALL,
            "to": token_address,
            "value": 0,
            "data": HexBytes(approve_data.hex()),
        }
    
    def _get_amounts_out(self, amount_in: int) -> Generator[None, None, int]:
        """
        Get the estimated output amount from Uniswap.

        :param amount_in: The amount of input tokens.
        :return: The estimated amount of output tokens.
        """

        path = [self.params.wxdai_contract_address, self.params.wbtc_contract_address]

        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self.params.uniswap_router_address,
            contract_id=str(UniswapV2Router02Contract.contract_id),
            contract_callable="get_amounts_out",
            amount_in=amount_in,
            path=path,
            chain_id=GNOSIS_CHAIN_ID,
        )

        if contract_api_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(f"Could not get amounts out: {contract_api_msg}")
            return 0  

        return contract_api_msg.state.body["amounts"][1]
    
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
            contract_id=str(ERC20.contract_id),
            contract_callable="build_deposit_tx",
            chain_id=GNOSIS_CHAIN_ID,
        )
        deposit_data = cast(bytes, contract_api_msg.raw_transaction.body["data"])

        return {
            "operation": MultiSendOperation.CALL,
            "to": self.params.wxdai_contract_address,
            "value": deposit_amount,  # Send the deposit amount
            "data": HexBytes(deposit_data.hex()),
        }
    
    def _prepare_swap_tx(self, amount_out_min) -> Generator[None, None, dict]:
        """
        Prepare the swap transaction on Uniswap.

        :return: The prepared swap transaction.
        """
        path = [self.params.wxdai_contract_address, self.params.wbtc_contract_address]
        deadline = int(datetime.now().timestamp() + 60)  # 60 seconds from now

        # Prepare the swap transaction
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.params.uniswap_router_address,
            contract_id=str(UniswapV2Router02Contract.contract_id),
            contract_callable="swap_exact_tokens_for_tokens",
            amount_in=self.params.trade_amount,
            amount_out_min=amount_out_min,
            path=path,
            to_address=self.synchronized_data.safe_contract_address,
            deadline=deadline,
            chain_id=GNOSIS_CHAIN_ID,
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
            contract_callable="add_log",
            amount=amount_swapped,
            sender_address=self.synchronized_data.safe_contract_address,
            chain_id=GNOSIS_CHAIN_ID,
        )
        deposit_tracker_data = cast(bytes, contract_api_msg.raw_transaction.body["data"])
        return {
            "operation": MultiSendOperation.CALL,
            "to": self.params.deposit_tracker_address,
            "value": 0,
            "data": HexBytes(deposit_tracker_data.hex()),
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
            chain_id=GNOSIS_CHAIN_ID
        )

        multisend_data = cast(str, contract_api_msg.raw_transaction.body["data"])
        multisend_data = multisend_data[2:]
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
            to_address=self.params.multisend_contract_address,
            value=0,
            data=multisend_data,
            operation=SafeOperation.DELEGATE_CALL.value,
            safe_tx_gas=SAFE_GAS,
            #safe_nonce=0,
            chain_id=GNOSIS_CHAIN_ID
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
