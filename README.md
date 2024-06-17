# Autonomous CPI Analysis and Trading Agent

## Overview

This project automates the process of analyzing the Consumer Price Index (CPI) published by the U.S. Bureau of Labor Statistics (BLS) and executing trades based on this data. The agent fetches the CPI data, compares it against consensus values from `fxstreet.com`, and performs automated trading on Uniswap if certain conditions are met. The system is designed to make timely investment decisions based on macroeconomic indicators, taking advantage of market movements.

## Workflow
The project uses a state machine with three key states: `ApiCheckState`, `DecisionMakingState`, and `TransactionState`. Each state has specific behaviors and rounds that handle the processes of data capture, decision-making, and transaction execution.

### 1. ApiCheckState

#### 1a. ApiCheckBehaviour

The `ApiCheckBehaviour` is responsible for gathering the latest CPI data from the BLS website and the consensus value from fxstreet.com. This data is crucial for making informed trading decisions.

- **Data Fetching:**

    - The agent fetches the CPI statement from `bls.gov`, extracts the CPI value, and uploads the full statement to IPFS for decentralized storage.
    - Simultaneously, it retrieves the consensus value for CPI from `fxstreet.com`, which represents the expected CPI value by market analysts.

- **Data Storage:**

    - The CPI statement is uploaded to `IPFS`, and the resulting hash is used to track changes in the data.

- **Outputs:**

    - **bls_index_value:** The CPI value extracted from the BLS statement.
    - **consensus_value:** The expected CPI value from `fxstreet.com`.
    - **hash_value:** The IPFS hash of the uploaded CPI statement.
    - **different_hash:** Indicates if the current IPFS hash differs from the previous one.

#### 1b. ApiCheckRound

The `ApiCheckRound` aggregates the data gathered by `ApiCheckBehaviour` and prepares it for decision-making.

- **Functionality:**

    - Aggregates the CPI data (bls_index_value) and the consensus value (consensus_value).
    - Utilizes the hash_value and different_hash to determine if there are updates to the CPI statement.
    - Includes a round_number to force trigger the app during development or demos.

- **Key Outputs:**

    - bls_index_value
    - consensus_value
    - hash_value (frozen for the period's cycle)
    - different_hash (compares the current and previous hashes)

### 2. DecisionMakingState
#### 2a. DecisionMakingBehaviour

The `DecisionMakingBehaviour` determines whether to initiate a Uniswap transaction based on the CPI data relative to the consensus value. The goal is to make trading decisions that can capitalize on market movements prompted by economic data releases.

- **Decision Criteria:**

    - Compares `bls_index_value` against `consensus_value`.

    - If `bls_index_value` is lower than `consensus_value` and `different_hash` is **TRUE** (indicating a new CPI release), a Uniswap transaction is initiated.
    
    - During development or demo, we will retrieve data from another site owned by us, where we can freely modify the statement

- **Outputs:**

    - `initiate_transaction`: A flag indicating whether the conditions for the transaction are met.

#### 2b. DecisionMakingRound

The `DecisionMakingRound` processes the decision made by `DecisionMakingBehaviour`.

- **Functionality:**

    - Evaluates if the conditions to trigger a transaction are satisfied based on the data and criteria.

    - If conditions are met, propagates the event to initiate the transaction.

    - If not, emits a `NO_MAJORITY` event to restart the cycle.

### 3. TransactionState

#### 3a. TxPreparationBehaviour

The `TxPreparationBehaviour` handles the preparation of complex financial transactions on Uniswap. This includes verifying balances, depositing funds if necessary, and preparing the execution of the swap while logging the transaction details.

- **Balance Verification:**
    - Checks the Safe's balances of `WxDAI` and `WBTC` to ensure there are sufficient funds for the transaction.
    - Fund Deposit: If there isn't enough `WxDAI`, deposits additional xDAI into the WxDAI contract.
- **Approval:**
    - Approves the Uniswap router to spend the required amount of WxDAI.
- **Slippage Calculation:**
    - Estimates the minimum amount of `WBTC` to receive, accounting for slippage to protect against market volatility.
- **Swap Execution:**
    Performs the swap of `WxDAI` for `WBTC` on Uniswap.
- **Transaction Logging:**
    Logs the completed transaction in the `DepositTracker` contract for record-keeping.

**Outputs:**

- **transaction_complete:** Indicates the successful execution of the transaction.

#### 3b. TxPreparationRound
The `TxPreparationRound` manages the completion of the transaction process.

- **Functionality:**
    - Executes the batch of transactions prepared by `TxPreparationBehaviour`.
    - Returns to the initial state upon successful transaction execution, ready to start a new cycle.

## Technical Modifications

The canonical state machine has been customized to support specific workflows for this project, particularly in the handling of data fetching, decision-making, and transaction execution.

### Data Fetching
- Integrated mechanisms to fetch CPI data from BLS.
- Added functionality to fetch and handle consensus data from fxstreet.com.
- Implemented IPFS integration to store the CPI statement.

### Decision-Making
- Enhanced error handling and data validation.
- Added logic to compare current and previous IPFS hashes to detect new data releases.
- Implemented conditional transitions to the transaction state based on CPI comparisons.

### Transaction Execution
- Developed sophisticated transaction management using MultiSend to handle batch transactions.
- Implemented interactions with ERC20 contracts to check balances and manage funds.
- Added approval mechanisms for the Uniswap router.
- Included slippage calculations to ensure favorable trade conditions.
- Integrated transaction logging with the DepositTracker contract for accountability.

# Installation
## System requirements

- Python `>=3.10`
- [Tendermint](https://docs.tendermint.com/v0.34/introduction/install.html) `==0.34.19`
- [IPFS node](https://docs.ipfs.io/install/command-line/#official-distributions) `==0.6.0`
- [Pip](https://pip.pypa.io/en/stable/installation/)
- [Poetry](https://python-poetry.org/)
- [Docker Engine](https://docs.docker.com/engine/install/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- [Set Docker permissions so you can run containers as non-root user](https://docs.docker.com/engine/install/linux-postinstall/)

1. Clone this repo:

    ```
    git clone git@github.com:clriesco/olas-final-project.git
    ```

2. Create the virtual environment:

    ```
    cd olas-final-project
    poetry shell
    poetry install
    ```

3. Sync packages:

    ```
    autonomy packages sync --update-packages
    ```
### Prepare the data

1. Prepare a keys.json file containing wallet address and the private key for each of the four agents.

    ```
    autonomy generate-key ethereum -n 4
    ```

2. Deploy a [Safe on Gnosis](https://app.safe.global/welcome) (it's free) and set your agent addresses as signers. Set the signature threshold to 3 out of 4.

3. Fund your agents and Safe with a small amount of xDAI, i.e. $0.02 each.


### Run the service

1. Make a copy of the env file:

    ```
    cp sample.env .env
    ```

2. Fill in the required environment variables in .env. These variables are: `ALL_PARTICIPANTS`, `GNOSIS_LEDGER_RPC`, `SAFE_CONTRACT_ADDRESS` and `USBLS_STATEMENT_PAGE`. You will need to get a [Tenderly](https://tenderly.co/) fork RPC (or alternatively an actual mainnet RPC if you want to run against the real chain).

3. Check that Docker is running:

    ```
    docker
    ```

4. Run the service:

    ```
    bash run_service.sh
    ```

5. Look at the service logs (on another terminal):

    ```
    docker logs -f learningservice_abci_0
    ```