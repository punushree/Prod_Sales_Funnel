from rest_framework import serializers
from .models import Organisation, User, Lead, Customer, CallLog, Transcript, CallAnalysis, Ticket


class OrganisationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organisation
        fields = "__all__"
        read_only_fields = ["id", "calls_used", "minutes_used", "created_at", "updated_at"]


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "org", "name", "email", "role", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class LeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = "__all__"
        read_only_fields = ["id", "onboarded_at"]


class TranscriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transcript
        fields = "__all__"
        read_only_fields = ["id", "created_at"]


class CallAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = CallAnalysis
        fields = "__all__"
        read_only_fields = ["id", "analysed_at"]


class TicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticket
        fields = "__all__"
        read_only_fields = ["id", "created_at"]


class CallLogSerializer(serializers.ModelSerializer):
    transcript    = TranscriptSerializer(read_only=True)
    analysis      = CallAnalysisSerializer(read_only=True)
    tickets       = TicketSerializer(many=True, read_only=True)

    class Meta:
        model = CallLog
        fields = "__all__"
        read_only_fields = ["id", "created_at"]