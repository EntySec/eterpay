"""
MIT License

Copyright (c) 2020-2023 EntySec

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import bit
import json

from uuid import uuid4
from decimal import Decimal

from rest_framework import viewsets
from rest_framework.decorators import api_view, action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication, TokenAuthentication

from bit.network.rates import currency_to_satoshi_cached

from django.shortcuts import render
from django.conf import settings

from bitcoin.models import BitcoinInvoice
from bitcoin.serializers import (
    InvoiceSerializer,
    CreateInvoice,
    WithdrawInvoice
)

from eterpay.utils import get_object_or_none


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = BitcoinInvoice.objects.all().order_by('uuid')
    serializer_class = InvoiceSerializer

    authentication_classes = [
        SessionAuthentication,
        BasicAuthentication,
        TokenAuthentication
    ]

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_anonymous:
            return []

        return self.queryset.filter(user=self.request.user)

    @action(detail=False, methods=['get'], url_path=r'delete/(?P<uuid>[0-9a-f-]+)')
    def delete_invoice(self, request, uuid):
        invoice = get_object_or_none(
            BitcoinInvoice, user=request.user, uuid=uuid)

        if not invoice:
            return Response(
                {'error': 'UUID does not belong to any of the invoices.'}, status=400)

        invoice.delete()
        return Response()

    @action(detail=False, methods=['get'], url_path=r'balance/(?P<uuid>[0-9a-f-]+)')
    def invoice_balance(self, request, uuid):
        invoice = get_object_or_none(
            BitcoinInvoice, user=request.user, uuid=uuid)

        if not invoice:
            return Response(
                {'error': 'UUID does not belong to any of the invoices.'}, status=400)

        if settings.TEST:
            wallet = bit.PrivateKeyTestnet(invoice.key)
        else:
            wallet = bit.PrivateKey(invoice.key)

        wallet.get_unspents()

        try:
            return Response(
                {'amount': wallet.get_balance('btc')})
        except Exception:
            return Response(
                {'error': 'Invalid currency provided.'}, status=400)

    @action(detail=False, methods=['get'], url_path=r'check/(?P<uuid>[0-9a-f-]+)')
    def check_invoice(self, request, uuid):
        invoice = get_object_or_none(
            BitcoinInvoice, user=request.user, uuid=uuid)

        if not invoice:
            return Response(
                {'error': 'UUID does not belong to any of the invoices.'}, status=400)

        if settings.TEST:
            wallet = bit.PrivateKeyTestnet(invoice.key)
        else:
            wallet = bit.PrivateKey(invoice.key)

        utxo = wallet.get_unspents()

        if len(utxo) > 0:
            last_utxo = utxo[-1]
            invoice_amount = currency_to_satoshi_cached(invoice.amount, 'btc')

            if last_utxo.amount < invoice_amount:
                return Response(
                    {
                        'error': f'Payed amount is less ({str(last_utxo.amount)} < {str(invoice_amount)})'
                    }, status=400)

            if last_utxo.confirmations < settings.BTC_CONFIRMATIONS:
                return Response(
                    {
                        'error': f'Payment is not confirmed yet ({str(last_utxo.confirmations)} < {settings.BTC_CONFIRMATIONS})'
                    }, status=400)

            return Response()

        return Response(
            {'error': f'Amount is not payed'}, status=400)

    @action(detail=False, methods=['post'], url_path=r'withdraw/(?P<uuid>[0-9a-f-]+)')
    def withdraw_invoice(self, request, uuid):
        serializer = WithdrawInvoice(data=request.POST)

        if not serializer.is_valid():
            return Response(
                {'error': serializer.errors}, status=400)

        invoice = get_object_or_none(
            BitcoinInvoice, user=request.user, uuid=uuid)

        if not invoice:
            return Response(
                {'error': 'UUID does not belong to any of the invoices.'}, status=400)

        if settings.TEST:
            wallet = bit.PrivateKeyTestnet(invoice.key)
        else:
            wallet = bit.PrivateKey(invoice.key)

        try:
            return Response(
                {'id': wallet.send([], leftover=serializer.data['address'])})

        except Exception as e:
            return Response(
                {'error': f'Fatal error occurred: {str(e)}'}, status=400)

    @action(detail=False, methods=['post'], url_path=r'release/(?P<uuid>[0-9a-f-]+)')
    def release_invoice(self, request, uuid):
        invoice = get_object_or_none(
            BitcoinInvoice, user=request.user, uuid=uuid)

        if not invoice:
            return Response(
                {'error': 'UUID does not belong to any of the invoices.'}, status=400)

        if settings.TEST:
            wallet = bit.PrivateKeyTestnet(invoice.key)
        else:
            wallet = bit.PrivateKey(invoice.key)

        vendors = json.loads(request.body)
        outputs = []

        for vendor in vendors:
            amount = Decimal(str(vendors[vendor]['amount']))
            fee = Decimal(str(vendors[vendor]['fee']))

            estimated_fee = get_price_fee(amount, fee)

            outputs.append((vendor, amount - estimated_fee, 'btc'))

        try:
            return Response(
                {'id': wallet.send(outputs)})

        except Exception as e:
            return Response(
                {'error': f'Fatal error occurred: {str(e)}'}, status=400)

    @action(detail=False, methods=['get'], url_path=r'details/(?P<uuid>[0-9a-f-]+)')
    def invoice_details(self, request, uuid):
        invoice = get_object_or_none(
            BitcoinInvoice, user=request.user, uuid=uuid)

        if not invoice:
            return Response(
                {'error': 'UUID does not belong to any of the invoices.'}, status=400)

        return Response(
            InvoiceSerializer(invoice).data
        )

    @action(detail=False, methods=['post'], url_path=r'create')
    def create_invoice(self, request):
        serializer = CreateInvoice(data=request.POST)

        if not serializer.is_valid():
            return Response(
                {'error': serializer.errors}, status=400)

        if settings.TEST:
            wallet = bit.PrivateKeyTestnet()
        else:
            wallet = bit.PrivateKey()

        invoice = BitcoinInvoice(
            amount=serializer.data['amount'],
            uuid=uuid4(),
            key=wallet.to_wif(),
            address=wallet.address,
            user=request.user
        )
        invoice.save()

        return Response(
            InvoiceSerializer(invoice).data
        )
