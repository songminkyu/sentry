import {Fragment, useState} from 'react';
import {css, useTheme} from '@emotion/react';
import styled from '@emotion/styled';
import orderBy from 'lodash/orderBy';

import {openModal} from 'sentry/actionCreators/modal';
import {openConfirmModal} from 'sentry/components/confirm';
import {Button} from 'sentry/components/core/button';
import type {MenuItemProps} from 'sentry/components/dropdownMenu';
import {DropdownMenu} from 'sentry/components/dropdownMenu';
import LoadingError from 'sentry/components/loadingError';
import LoadingIndicator from 'sentry/components/loadingIndicator';
import {CreateSavedSearchModal} from 'sentry/components/modals/savedSearchModal/createSavedSearchModal';
import {EditSavedSearchModal} from 'sentry/components/modals/savedSearchModal/editSavedSearchModal';
import {IconClose, IconEllipsis} from 'sentry/icons';
import {t} from 'sentry/locale';
import {space} from 'sentry/styles/space';
import type {SavedSearch} from 'sentry/types/group';
import {SavedSearchVisibility} from 'sentry/types/group';
import type {Organization} from 'sentry/types/organization';
import {trackAnalytics} from 'sentry/utils/analytics';
import useMedia from 'sentry/utils/useMedia';
import {useSyncedLocalStorageState} from 'sentry/utils/useSyncedLocalStorageState';
import {useDeleteSavedSearchOptimistic} from 'sentry/views/issueList/mutations/useDeleteSavedSearch';
import {useFetchSavedSearchesForOrg} from 'sentry/views/issueList/queries/useFetchSavedSearchesForOrg';
import {SAVED_SEARCHES_SIDEBAR_OPEN_LOCALSTORAGE_KEY} from 'sentry/views/issueList/utils';
import {usePrefersStackedNav} from 'sentry/views/nav/usePrefersStackedNav';

interface SavedIssueSearchesProps {
  onSavedSearchSelect: (savedSearch: SavedSearch) => void;
  organization: Organization;
  query: string;
  sort: string;
}

interface SavedSearchItemProps
  extends Pick<SavedIssueSearchesProps, 'organization' | 'onSavedSearchSelect'> {
  savedSearch: SavedSearch;
}

type CreateNewSavedSearchButtonProps = Pick<
  SavedIssueSearchesProps,
  'query' | 'sort' | 'organization'
>;

const MAX_SHOWN_SEARCHES = 4;

function SavedSearchItemDescription({
  savedSearch,
}: Pick<SavedSearchItemProps, 'savedSearch'>) {
  if (savedSearch.isGlobal) {
    return <SavedSearchItemQuery>{savedSearch.query}</SavedSearchItemQuery>;
  }

  return (
    <SavedSearchItemVisbility>
      {savedSearch.visibility === SavedSearchVisibility.ORGANIZATION
        ? t('Anyone in organization can see but not edit')
        : t('Only you can see and edit')}
    </SavedSearchItemVisbility>
  );
}

function SavedSearchItem({
  organization,
  onSavedSearchSelect,
  savedSearch,
}: SavedSearchItemProps) {
  const {mutate: deleteSavedSearch} = useDeleteSavedSearchOptimistic();
  const hasOrgWriteAccess = organization.access?.includes('org:write');

  const canEdit =
    savedSearch.visibility === SavedSearchVisibility.OWNER || hasOrgWriteAccess;

  const actions: MenuItemProps[] = [
    {
      key: 'edit',
      label: 'Edit',
      disabled: !canEdit,
      details: canEdit ? undefined : t('You do not have permission to edit this search.'),
      onAction: () => {
        openModal(deps => (
          <EditSavedSearchModal {...deps} {...{organization, savedSearch}} />
        ));
      },
    },
    {
      disabled: !canEdit,
      details: canEdit
        ? undefined
        : t('You do not have permission to delete this search.'),
      key: 'delete',
      label: t('Delete'),
      onAction: () => {
        openConfirmModal({
          message: t('Are you sure you want to delete this saved search?'),
          onConfirm: () =>
            deleteSavedSearch({orgSlug: organization.slug, id: savedSearch.id}),
        });
      },
      priority: 'danger',
    },
  ];

  return (
    <SearchListItem hasMenu={!savedSearch.isGlobal}>
      <StyledItemButton
        aria-label={savedSearch.name}
        onClick={() => onSavedSearchSelect(savedSearch)}
        borderless
      >
        <TitleDescriptionWrapper>
          <SavedSearchItemTitle>{savedSearch.name}</SavedSearchItemTitle>
          <SavedSearchItemDescription savedSearch={savedSearch} />
        </TitleDescriptionWrapper>
      </StyledItemButton>
      {!savedSearch.isGlobal && (
        <OverflowMenu
          position="bottom-end"
          items={actions}
          size="sm"
          minMenuWidth={200}
          trigger={props => (
            <Button
              {...props}
              aria-label={t('Saved search options')}
              borderless
              icon={<IconEllipsis size="sm" />}
              size="sm"
            />
          )}
        />
      )}
    </SearchListItem>
  );
}

function CreateNewSavedSearchButton({
  organization,
  query,
  sort,
}: CreateNewSavedSearchButtonProps) {
  const onClick = () => {
    trackAnalytics('search.saved_search_open_create_modal', {
      organization,
    });
    openModal(deps => (
      <CreateSavedSearchModal {...deps} {...{organization, query, sort}} />
    ));
  };

  return (
    <Button onClick={onClick} priority="link" size="sm">
      {t('Add saved search')}
    </Button>
  );
}

function SavedIssueSearches({
  organization,
  onSavedSearchSelect,
  query,
  sort,
}: SavedIssueSearchesProps) {
  const theme = useTheme();
  const [isOpen, setIsOpen] = useSyncedLocalStorageState(
    SAVED_SEARCHES_SIDEBAR_OPEN_LOCALSTORAGE_KEY,
    false
  );
  const [showAll, setShowAll] = useState(false);
  const {
    data: savedSearches,
    isPending,
    isError,
    refetch,
  } = useFetchSavedSearchesForOrg({orgSlug: organization.slug});
  const isMobile = useMedia(`(max-width: ${theme.breakpoints.sm})`);
  const prefersStackedNav = usePrefersStackedNav();

  const shouldShowSavedSearches = !prefersStackedNav;

  if (!isOpen || isMobile || !shouldShowSavedSearches) {
    return null;
  }

  if (isPending) {
    return (
      <StyledSidebar>
        <LoadingIndicator />
      </StyledSidebar>
    );
  }

  if (isError) {
    return (
      <StyledSidebar>
        <LoadingError onRetry={refetch} />
      </StyledSidebar>
    );
  }

  const orgSavedSearches = orderBy(
    savedSearches.filter(search => !search.isGlobal && !search.isPinned),
    'dateCreated',
    'desc'
  );

  const recommendedSavedSearches = savedSearches.filter(search => search.isGlobal);

  const shownOrgSavedSearches = showAll
    ? orgSavedSearches
    : orgSavedSearches.slice(0, MAX_SHOWN_SEARCHES);

  return (
    <StyledSidebar>
      <Fragment>
        <HeadingContainer>
          <Heading>{t('Saved Searches')}</Heading>
          <Button
            aria-label={t('Collapse sidebar')}
            borderless
            onClick={() => setIsOpen(false)}
            icon={<IconClose />}
          />
        </HeadingContainer>
        <CreateSavedSearchWrapper>
          <CreateNewSavedSearchButton {...{organization, query, sort}} />
        </CreateSavedSearchWrapper>
        <SearchesContainer>
          {shownOrgSavedSearches.map(item => (
            <SavedSearchItem
              key={item.id}
              organization={organization}
              onSavedSearchSelect={onSavedSearchSelect}
              savedSearch={item}
            />
          ))}
          {shownOrgSavedSearches.length === 0 && (
            <NoSavedSearchesText>
              {t("You don't have any saved searches")}
            </NoSavedSearchesText>
          )}
        </SearchesContainer>
        {orgSavedSearches.length > shownOrgSavedSearches.length && (
          <ShowAllButton size="zero" borderless onClick={() => setShowAll(true)}>
            {t(
              'Show %s more',
              (orgSavedSearches.length - shownOrgSavedSearches.length).toLocaleString()
            )}
          </ShowAllButton>
        )}
      </Fragment>
      {recommendedSavedSearches.length > 0 && (
        <Fragment>
          <HeadingContainer>
            <Heading>{t('Recommended Searches')}</Heading>
          </HeadingContainer>
          <SearchesContainer>
            {recommendedSavedSearches.map(item => (
              <SavedSearchItem
                key={item.id}
                organization={organization}
                onSavedSearchSelect={onSavedSearchSelect}
                savedSearch={item}
              />
            ))}
          </SearchesContainer>
        </Fragment>
      )}
    </StyledSidebar>
  );
}

const StyledSidebar = styled('aside')`
  grid-area: saved-searches;
  width: 100%;
  padding: ${space(2)};

  @media (max-width: ${p => p.theme.breakpoints.sm}) {
    border-bottom: 1px solid ${p => p.theme.gray200};
    padding: ${space(2)} 0;
  }

  @media (min-width: ${p => p.theme.breakpoints.sm}) {
    border-left: 1px solid ${p => p.theme.gray200};
    max-width: 340px;
  }
`;

const HeadingContainer = styled('div')`
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 38px;
  padding-left: ${space(2)};
  margin-top: ${space(3)};

  &:first-of-type {
    margin-top: 0;
  }
`;

const Heading = styled('h2')`
  font-size: ${p => p.theme.fontSize.xl};
  margin: 0;
`;

const CreateSavedSearchWrapper = styled('div')`
  padding: 0 ${space(2)};
  margin-bottom: ${space(1)};
`;

const SearchesContainer = styled('ul')`
  padding: 0;
  margin-bottom: ${space(1)};
`;

const StyledItemButton = styled(Button)`
  display: block;
  width: 100%;
  text-align: left;
  height: auto;
  font-weight: ${p => p.theme.fontWeight.normal};
  line-height: ${p => p.theme.text.lineHeightBody};

  padding: ${space(1)} ${space(2)};
`;

const OverflowMenu = styled(DropdownMenu)`
  display: block;
  position: absolute;
  top: 12px;
  right: ${space(1)};
`;

const SearchListItem = styled('li')<{hasMenu?: boolean}>`
  position: relative;
  list-style: none;
  padding: 0;
  margin: 0;

  ${p =>
    p.hasMenu &&
    css`
      @media (max-width: ${p.theme.breakpoints.sm}) {
        ${StyledItemButton} {
          padding-right: 60px;
        }
      }

      @media (min-width: ${p.theme.breakpoints.sm}) {
        ${OverflowMenu} {
          display: none;
        }

        &:hover,
        &:focus-within {
          ${OverflowMenu} {
            display: block;
          }

          ${StyledItemButton} {
            padding-right: 60px;
          }
        }
      }
    `}
`;

const TitleDescriptionWrapper = styled('div')`
  overflow: hidden;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: start;
  width: 100%;
`;

const SavedSearchItemTitle = styled('div')`
  text-align: left;
  font-size: ${p => p.theme.fontSize.lg};
  ${p => p.theme.overflowEllipsis}
`;

const SavedSearchItemVisbility = styled('div')`
  color: ${p => p.theme.subText};
  font-size: ${p => p.theme.fontSize.sm};
  ${p => p.theme.overflowEllipsis}
`;

const SavedSearchItemQuery = styled('div')`
  font-family: ${p => p.theme.text.familyMono};
  font-size: ${p => p.theme.fontSize.sm};
  color: ${p => p.theme.subText};
  ${p => p.theme.overflowEllipsis}
`;

const ShowAllButton = styled(Button)`
  color: ${p => p.theme.linkColor};
  font-weight: ${p => p.theme.fontWeight.normal};
  padding: ${space(0.5)} ${space(2)};

  &:hover {
    color: ${p => p.theme.linkHoverColor};
  }
`;

const NoSavedSearchesText = styled('p')`
  padding: 0 ${space(2)};
  margin: ${space(0.5)} 0;
  color: ${p => p.theme.subText};
`;

export default SavedIssueSearches;
