# 06-06-25 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


from django import forms
from GUI.searchapp.api import get_available_sites


def get_site_choices():
    sites = get_available_sites()
    return [(site, site.replace('_', ' ').title()) for site in sites]


class SearchForm(forms.Form):
    site = forms.ChoiceField(
        label="Site",
        widget=forms.Select(
            attrs={
                "class": "block w-full appearance-none rounded-xl border-2 border-gray-800 bg-black/50 py-4 pl-6 pr-12 text-white text-lg font-medium cursor-pointer focus:border-red-600 focus:outline-none focus:ring-4 focus:ring-red-600/20 transition-all",
                "id": "id_site",
            }
        ),
    )
    query = forms.CharField(
        max_length=200,
        label="What are you looking for?",
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-xl border-2 border-gray-800 bg-black/50 py-4 pl-6 pr-6 text-white text-lg placeholder-gray-600 focus:border-red-600 focus:outline-none focus:ring-4 focus:ring-red-600/20 transition-all",
                "placeholder": "Search title...",
                "autocomplete": "off",
            }
        ),
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].choices = get_site_choices()


class DownloadForm(forms.Form):
    source_alias = forms.CharField(widget=forms.HiddenInput)
    item_payload = forms.CharField(widget=forms.HiddenInput, required=False)
    season = forms.CharField(max_length=100, required=False, label="Season")
    episode = forms.CharField(max_length=1000, required=False, label="Episode (e.g. 1-3)")