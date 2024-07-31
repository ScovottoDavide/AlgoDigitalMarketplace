import algokit_utils
import algosdk
import pytest
from algokit_utils.beta.account_manager import AddressAndSigner
from algokit_utils.beta.algorand_client import (
    AlgorandClient,
    AssetCreateParams,
    AssetOptInParams,
    AssetTransferParams,
    PayParams,
)
from algosdk.atomic_transaction_composer import TransactionWithSigner

from smart_contracts.algo_digital_marketplace.contract import AlgoDigitalMarketplace
from smart_contracts.artifacts.algo_digital_marketplace.algo_digital_marketplace_client import AlgoDigitalMarketplaceClient

# Fixtures
## Algorand Client
@pytest.fixture(scope="session")
def algorand() -> AlgorandClient:
    return AlgorandClient.default_local_net()

## Dispenser - account that has testing algos, to fund accounts needed for testing
@pytest.fixture(scope="session")
def dispenser(algorand: AlgorandClient) -> AddressAndSigner:
    return algorand.account.dispenser()

## Creator Account - creator of contracts (Seller user)
@pytest.fixture(scope="session")
def creator(algorand: AlgorandClient, dispenser: AddressAndSigner) -> AddressAndSigner:
    account = algorand.account.random()
    algorand.send.payment(
        PayParams(
            sender=dispenser.address,
            receiver=account.address,
            amount=10_000_000, # 10 algos
            # signer=dispenser.signer,
        )
    )
    return account

## Test Asset ID - create the asset and retrieve the ID
@pytest.fixture(scope="session")
def test_asset_id(algorand: AlgorandClient, creator: AddressAndSigner) -> int:
    sent_tx = algorand.send.asset_create(
        AssetCreateParams(
            sender=creator.address,
            total=10,
        )
    )
    return sent_tx["confirmation"]["asset-index"]

## Smart Contract (Digital Markeplace) client (creates also the contract) 
#   - to call contract methods and setup the inputs to those methods 
@pytest.fixture(scope="session")
def algo_digital_marketplace_client(
    algorand: AlgorandClient,
    creator: AddressAndSigner,
    test_asset_id: int,
) -> AlgoDigitalMarketplaceClient:
    client = AlgoDigitalMarketplaceClient(
        algod_client=algorand.client.algod,
        sender=creator.address,
        signer=creator.signer
    )

    client.create_create_application(
        assetId=test_asset_id,
        unitaryPrice=0,
    )
    return client


# Tests for the methods
## Optin
def test_opt_in_to_asset(
    algorand: AlgorandClient,
    creator: AddressAndSigner,
    algo_digital_marketplace_client: AlgoDigitalMarketplaceClient,
    test_asset_id: int,
):
    # cover the minimum balance requirement: 
    # when opting-in the mbr of the contract must increase:
    #  100_000 for activating the contract
    # + 100_000 for the asset opt-in
    mbr_pay_txn = algorand.transactions.payment(
        PayParams(
            sender=creator.address,
            receiver=algo_digital_marketplace_client.app_address,
            amount=200_000,
            extra_fee=1_000
        )
    )
    result = algo_digital_marketplace_client.opt_in_to_asset(
        mbrPay=TransactionWithSigner(txn=mbr_pay_txn, signer=creator.signer),
        transaction_parameters=algokit_utils.TransactionParameters(
            foreign_assets=[test_asset_id]
        )
    )
    # Test tx is executed
    assert result.confirmed_round

    # Test contract is opted-in
    asset_amount_of_contract = algorand.account.get_asset_information(
        algo_digital_marketplace_client.app_address,
        test_asset_id
    )["asset-holding"]["amount"]
    assert asset_amount_of_contract == 0

## Deposit
def test_deposit(
    algorand: AlgorandClient,
    creator: AddressAndSigner,
    algo_digital_marketplace_client: AlgoDigitalMarketplaceClient,
    test_asset_id: int,
):
    result = algorand.send.asset_transfer(
        AssetTransferParams(
            sender=creator.address,
            receiver=algo_digital_marketplace_client.app_address,
            asset_id=test_asset_id,
            amount=3,
        )
    )

    # make sure the tx was successfull
    assert result["confimation"]
    
    # make sure the app now has 3 assets
    assert (
        algorand.account.get_asset_information(
            algo_digital_marketplace_client.app_address, test_asset_id
        )["asset-holding"]["amount"]
        == 3
    )

## Set Price
def test_set_price(algo_digital_marketplace_client: AlgoDigitalMarketplaceClient):
    result = algo_digital_marketplace_client.set_price(unitary_price=3_300_000)

    assert result.confirmed_round

## Buying 
def test_buy(
    algo_digital_marketplace_client: AlgoDigitalMarketplaceClient,
    test_asset_id: int,
    algorand: AlgorandClient,
    dispenser: AddressAndSigner,
):
    # create new account to be the buyer
    buyer = algorand.account.random()

    # use the dispenser to fund buyer
    algorand.send.payment(
        PayParams(sender=dispenser.address, receiver=buyer.address, amount=10_000_000)
    )

    # opt the buyer into the asset
    algorand.send.asset_opt_in(
        AssetOptInParams(sender=buyer.address, asset_id=test_asset_id)
    )

    # form a transaction to buy two assets (2 * 3_300_000)
    buyer_payment_txn = algorand.transactions.payment(
        PayParams(
            sender=buyer.address,
            receiver=algo_digital_marketplace_client.app_address,
            amount=2 * 3_300_000,
            extra_fee=1_000,
        )
    )

    result = algo_digital_marketplace_client.buy(
        buyer_txn=TransactionWithSigner(txn=buyer_payment_txn, signer=buyer.signer),
        quantity=2,
        transaction_parameters=algokit_utils.TransactionParameters(
            sender=buyer.address,
            signer=buyer.signer,
            # we need to tell the AVM about the asset the call will use
            foreign_assets=[test_asset_id],
        ),
    )

    assert result.confirmed_round

    assert (
        algorand.account.get_asset_information(buyer.address, test_asset_id)[
            "asset-holding"
        ]["amount"]
        == 2
    )

## Delete or withdraw
def test_delete_application(
    algo_digital_marketplace_client: AlgoDigitalMarketplaceClient,
    creator: AddressAndSigner,
    test_asset_id: int,
    algorand: AlgorandClient,
):
    # Get the balance of the creator before we delete so we can measure the effect of the deletion
    before_call_amount = algorand.account.get_information(creator.address)["amount"]

    result = algo_digital_marketplace_client.delete_delete_application(
        transaction_parameters=algokit_utils.TransactionParameters(
            # we are sending the asset in the call, so we need to tell the AVM
            foreign_assets=[test_asset_id],
        )
    )

    assert result.confirmed_round

    after_call_amount = algorand.account.get_information(creator.address)["amount"]

    # Make sure the creator got all of the remaning assets and the remaining balance in the contract (minus fees)
    # 2 * 3_300_000 for the ALGO we got from sales
    # 200_000 for the MBR ALGO in the app that gets unlocked by opting out and closing the account
    # -3_000 for the fees
    assert after_call_amount - before_call_amount == (2 * 3_300_000) + 200_000 - 3_000
    # We sold two assets, so the creator should get 8 back
    assert (
        algorand.account.get_asset_information(creator.address, test_asset_id)[
            "asset-holding"
        ]["amount"]
        == 8
    )
