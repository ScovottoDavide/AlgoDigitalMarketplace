# pyright: reportMissingModuleSource=false
from algopy import (
    # On Algorand, assets are native objects rather than smart contracts
    ARC4Contract,
    Asset,
    # Global is used to access global variables from the network
    Global,
    # Txn is used access information about the current transcation
    Txn,
    # By default, all numbers in the AVM are 64-bit unsigned integers
    UInt64,
    # ARC4 defines the Algorand ABI for method calling and type encoding
    arc4,
    # gtxn is used to read transaction within the same atomic group
    gtxn,
    # itxn is used to send transactions from within a smart contract
    itxn,
)


class AlgoDigitalMarketplace(ARC4Contract):
    assetId: UInt64
    unitaryPrice: UInt64

    # create the application
    @arc4.abimethod(allow_actions=["NoOp"], create="require")
    def createApplication(self, assetId: Asset, unitaryPrice: UInt64) -> None:
        self.assetId = assetId.id
        self.unitaryPrice = unitaryPrice

    # update the listing price: externally called
    # Make sure this can be called only by the owner
    @arc4.abimethod
    def setPrice(self, unitaryPrice: UInt64) -> None:
        assert Txn.sender == Global.creator_address
        self.unitaryPrice = unitaryPrice

    # opt in to the asset that will be sold
    # externally available method
    # opt-in -> asset transfer to no one (?)
    @arc4.abimethod
    def optInToAsset(self, mbrPay: gtxn.PaymentTransaction) -> None:
        assert Txn.sender == Global.creator_address
        assert not Global.current_application_address.is_opted_in(Asset(self.assetId))
        assert mbrPay.receiver == Global.current_application_address
        assert mbrPay.amount == Global.min_balance + Global.asset_opt_in_min_balance

        itxn.AssetTransfer(
            xfer_asset=self.assetId,
            asset_receiver=Global.current_application_address,
            asset_amount=0
        ).submit()

    # buy the asset
    @arc4.abimethod
    def buy(self, buyerTxn: gtxn.PaymentTransaction, quantity: UInt64) -> None:
        assert self.unitaryPrice != UInt64(0)
        assert Txn.sender == buyerTxn.sender
        assert buyerTxn.receiver == Global.current_application_address
        assert buyerTxn.amount == self.unitaryPrice * quantity

        itxn.AssetTransfer(
            xfer_asset=self.assetId,
            asset_receiver=Txn.sender,
            asset_amount=quantity,
        ).submit()

    # delete the application
    # the creator could call this to delete the SC
    @arc4.abimethod(allow_actions=['DeleteApplication'])
    def deleteApplication(self) -> None:
        assert Txn.sender == Global.creator_address

        # withdraw remaining assets
        itxn.AssetTransfer(
            xfer_asset=self.assetId,
            asset_receiver=Global.creator_address,
            asset_amount=0,
            asset_close_to=Global.creator_address, # send remaining assets to creator
        ).submit()        

        # withdraw remaining profits (algos)
        itxn.Payment(
            receiver=Global.creator_address,
            amount=0,
            close_remainder_to=Global.creator_address, # send all the algos that are in the SC
        ).submit()

